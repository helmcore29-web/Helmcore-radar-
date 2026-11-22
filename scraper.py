import os
import json
import hashlib
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# --- NASTAVENIA PRE HELMCORE S.R.O. ---
MIN_VALUE_EUR = 5000.0
DATA_FILE = "docs/tenders.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Kľúčové slová pre rýchle prefiltrovanie pred AI analýzou
KEYWORDS = [
    "elektroinštal", "rozvádzač", "silnoprúd", "slaboprúd", "bleskozvod",
    "fotovolt", "ftv", "eps", "požiarna signalizácia", "ezs", "zabezpeč",
    "kamerov", "cctv", "osvetlen", "dali", "kábl", "revízi", "opaos", "vtz", "nn prípojk"
]


class TenderEvaluation(BaseModel):
    is_relevant: bool = Field(description="True ak je zákazka vhodná pre HelMcore s.r.o. (región Východné SK, elektro/slaboprúd/FTV/bleskozvody, hodnota >= 5000 EUR alebo nešpecifikovaná odhadovaná pri väčšom rozsahu).")
    score: int = Field(description="Skóre zhody od 0 do 100.")
    estimated_value_eur: float = Field(description="Odhadovaná hodnota v EUR bez DPH. Ak nie je uvedená, odhadni reálnu trhovú cenu podľa rozsahu alebo daj 0.")
    location: str = Field(description="Mesto alebo okres realizácie (napr. Košice, Prešov, Poprad, Michalovce).")
    summary: str = Field(description="Stručný technický popis rozsahu prác v 2-3 vetách.")
    helmcore_advantage: str = Field(description="Prečo sa to oplatí pre HelMcore (napr. požiadavka na VTZ, § 23, výroba rozvádzača, EPS).")
    risks: str = Field(description="Zmluvné alebo technické riziká (splatnosť, zádržné, krátky termín), inak 'Bez zjavných rizík'.")
    deadline: str = Field(description="Termín na predloženie ponúk alebo 'Neuvedené'.")


def load_existing_tenders() -> list:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_tenders(tenders: list):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    # Zoradiť od najnovších a najvyššieho skóre
    tenders.sort(key=lambda x: (x.get("found_at", ""), x.get("score", 0)), reverse=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tenders[:150], f, ensure_ascii=False, indent=2)


def make_id(source: str, title: str, url: str) -> str:
    raw = f"{source}:{title.strip().lower()}:{url.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fetch_josephine_tenders() -> list:
    """Stiahne otvorené verejné zákazky z portálu JOSEPHINE (PROEBIZ)."""
    results = []
    print("Sťahujem zákazky z JOSEPHINE...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = context.new_page()
            page.goto("https://josephine.proebiz.com/sk/public-tenders/all", timeout=45000)
            page.wait_for_timeout(4000)

            # Vyhľadanie položiek v zozname zákaziek
            links = page.query_selector_all("a[href*='/public-tender/']")
            seen_urls = set()

            for link in links:
                href = link.get_attribute("href")
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)
                full_url = href if href.startswith("http") else f"https://josephine.proebiz.com{href}"
                text_block = link.inner_text().strip()

                # Základný filter kľúčových slov ešte pred otvorením detailu
                lower_text = text_block.lower()
                if any(kw in lower_text for kw in KEYWORDS):
                    results.append({
                        "source": "JOSEPHINE",
                        "title": text_block.split("\n")[0][:180],
                        "raw_text": text_block,
                        "url": full_url
                    })
            browser.close()
    except Exception as e:
        print(f"Chyba pri čítaní JOSEPHINE: {e}")
    return results


def fetch_uvo_tenders() -> list:
    """Stiahne aktuálne oznámenia z Vestníka ÚVO pre elektro CPV kódy."""
    results = []
    print("Sťahujem zákazky z Vestníka ÚVO...")
    cpv_codes = ["45310000-3", "45311000-0", "45312000-7", "45315000-8", "45316000-5"]
    try:
        for cpv in cpv_codes:
            url = f"https://www.uvo.gov.sk/vyhladavanie/vyhladavanie-zakaziek?cpv={cpv}"
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for row in soup.select("table tbody tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                link_el = row.find("a", href=True)
                if len(cols) >= 2 and link_el:
                    title = cols[0]
                    href = link_el["href"]
                    full_url = href if href.startswith("http") else f"https://www.uvo.gov.sk{href}"
                    results.append({
                        "source": "ÚVO",
                        "title": title[:180],
                        "raw_text": " | ".join(cols),
                        "url": full_url
                    })
    except Exception as e:
        print(f"Chyba pri čítaní ÚVO: {e}")
    return results


def enrich_detail_text(item: dict) -> str:
    """Otvorí detail zákazky, aby Gemini videlo lokalitu, sumu a technický popis."""
    try:
        resp = requests.get(item["url"], timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            body_text = soup.get_text(" ", strip=True)
            return body_text[:7000]
    except Exception:
        pass
    return item["raw_text"]


def evaluate_with_gemini(client: genai.Client, item: dict, detail_text: str) -> TenderEvaluation | None:
    system_prompt = """
    Si obchodný a technický analytik pre slovenskú elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Profil firmy HelMcore s.r.o.:
    - Oprávnenia: VTZ elektrické, odborná spôsobilosť § 23 vrátane prostredia s nebezpečenstvom výbuchu (Trieda B / Ex), registrácia v EVO.
    - Zameranie: silnoprúdové a slaboprúdové elektroinštalácie (priemysel, komerčné objekty, bytové domy), výroba NN rozvádzačov na mieru, bleskozvody (vrátane HVI), fotovoltika (FTV), EPS (Aritech, Kidde), EZS (Ajax), CCTV (Dahua), štruktúrovaná kabeláž a optika, inteligentné inštalácie (Ampio, DALI).
    - Región: Košice a okruh do 100 km (Košický kraj, Prešovský kraj – Prešov, Poprad, Spišská Nová Ves, Michalovce, Humenné, Trebišov, Rožňava).
    - Minimálna hodnota zákazky: 5 000 EUR bez DPH.

    Pravidlá vyhodnotenia:
    1. Ak je miesto realizácie mimo Východného Slovenska (napr. Bratislava, Trnava, Žilina, Nitra, Banská Bystrica), nastav is_relevant = False a score = 0 (výnimka len pre obrovské zákazky nad 150 000 EUR).
    2. Ak je uvedená predpokladaná hodnota zákazky a je nižšia ako 5 000 EUR bez DPH, nastav is_relevant = False. (Ak suma nie je uvedená priamo v texte, ale rozsah prác zjavne presahuje 5 000 EUR, nechaj is_relevant = True a odhadni hodnotu).
    3. Vyššie skóre (80-100) daj zákazkám, kde je výhoda oprávnenie VTZ, § 23, výroba rozvádzačov, EPS alebo bleskozvody.
    """

    user_prompt = f"""
    Analyzuj túto verejnú zákazku:
    Zdroj: {item['source']}
    Názov: {item['title']}
    URL: {item['url']}
    Text detailu:
    {detail_text}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=TenderEvaluation,
                temperature=0.1,
            ),
        )
        return TenderEvaluation.model_validate_json(response.text)
    except Exception as e:
        print(f"Chyba Gemini API pri '{item['title']}': {e}")
        return None


def send_telegram_notification(tender: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    val_str = f"{tender['estimated_value_eur']:,.0f} € bez DPH".replace(",", " ") if tender["estimated_value_eur"] > 0 else "Neuvedená (odhad > 5 000 €)"
    msg = (
        f"🔥 *ZHODA {tender['score']} % | {tender['source']}*\n"
        f"*{tender['title']}*\n\n"
        f"📍 *Lokalita:* {tender['location']}\n"
        f"💶 *Hodnota:* {val_str}\n"
        f"⏳ *Termín:* {tender['deadline']}\n"
        f"🛠 *Rozsah:* {tender['summary']}\n"
        f"✅ *Výhoda HelMcore:* {tender['helmcore_advantage']}\n"
        f"⚠️ *Riziká:* {tender['risks']}\n\n"
        f"🔗 [Otvoriť zákazku]({tender['url']})"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=15
        )
    except Exception as e:
        print(f"Chyba Telegram notifikácie: {e}")


def main():
    if not GEMINI_API_KEY:
        raise RuntimeError("Chýba GEMINI_API_KEY v GitHub Secrets!")

    client = genai.Client(api_key=GEMINI_API_KEY)
    existing = load_existing_tenders()
    existing_ids = {t["id"] for t in existing}

    raw_candidates = fetch_josephine_tenders() + fetch_uvo_tenders()
    print(f"Nájdených {len(raw_candidates)} kandidátov pred AI filtrom.")

    new_count = 0
    for item in raw_candidates:
        tid = make_id(item["source"], item["title"], item["url"])
        if tid in existing_ids:
            continue

        detail_text = enrich_detail_text(item)
        eval_res = evaluate_with_gemini(client, item, detail_text)
        if not eval_res:
            continue

        # Striktný filter: relevantné, skóre >= 70 a suma >= 5000 € (alebo neuvedená s vysokým rozsahom)
        if eval_res.is_relevant and eval_res.score >= 70:
            if eval_res.estimated_value_eur > 0 and eval_res.estimated_value_eur < MIN_VALUE_EUR:
                continue

            record = {
                "id": tid,
                "source": item["source"],
                "title": item["title"],
                "url": item["url"],
                "score": eval_res.score,
                "estimated_value_eur": eval_res.estimated_value_eur,
                "location": eval_res.location,
                "summary": eval_res.summary,
                "helmcore_advantage": eval_res.helmcore_advantage,
                "risks": eval_res.risks,
                "deadline": eval_res.deadline,
                "found_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            }
            existing.append(record)
            existing_ids.add(tid)
            new_count += 1
            send_telegram_notification(record)

    save_tenders(existing)
    print(f"Hotovo. Pridaných {new_count} nových relevantných zákaziek nad 5 000 €.")


if __name__ == "__main__":
    main()
