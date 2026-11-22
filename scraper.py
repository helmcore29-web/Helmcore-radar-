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

KEYWORDS = [
    "elektroinštal", "rozvádzač", "silnoprúd", "slaboprúd", "bleskozvod",
    "fotovolt", "ftv", "eps", "požiarna signalizácia", "ezs", "zabezpeč",
    "kamerov", "cctv", "osvetlen", "dali", "kábl", "revízi", "opaos", "vtz",
    "nn prípojk", "výrobná hala", "bytový dom", "rekonštrukcia objektu", "trafostanic"
]


class TenderEvaluation(BaseModel):
    is_relevant: bool = Field(description="True ak je zákazka alebo stavebný zámer vhodný pre HelMcore s.r.o. (Východné SK, elektro/slaboprúd/FTV/bleskozvody alebo subdodávka pre stavbu, hodnota >= 5000 EUR).")
    score: int = Field(description="Skóre zhody od 0 do 100.")
    estimated_value_eur: float = Field(description="Odhadovaná hodnota elektro časti v EUR bez DPH. Ak nie je uvedená, odhadni reálnu trhovú cenu podľa rozsahu.")
    location: str = Field(description="Mesto alebo okres realizácie (napr. Košice, Prešov, Poprad, Michalovce).")
    summary: str = Field(description="Stručný technický popis rozsahu prác alebo typu stavby (pri stavebnej firme uveď, akú subdodávku treba ponúknuť).")
    helmcore_advantage: str = Field(description="Prečo sa to oplatí pre HelMcore (VTZ, § 23, výroba rozvádzača, EPS, subdodávka pre GD).")
    risks: str = Field(description="Zmluvné alebo obchodné riziká (splatnosť, zádržné, len zber dopytov), inak 'Bez zjavných rizík'.")
    deadline: str = Field(description="Termín ponuky / realizácie alebo 'Priebežne / Osloviť čo najskôr'.")


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
    tenders.sort(key=lambda x: (x.get("found_at", ""), x.get("score", 0)), reverse=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tenders[:200], f, ensure_ascii=False, indent=2)


def make_id(source: str, title: str, url: str) -> str:
    raw = f"{source}:{title.strip().lower()}:{url.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def fetch_josephine_tenders() -> list:
    results = []
    print("1/5 Sťahujem JOSEPHINE...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = context.new_page()
            page.goto("https://josephine.proebiz.com/sk/public-tenders/all", timeout=45000)
            page.wait_for_timeout(4000)

            for link in page.query_selector_all("a[href*='/public-tender/']"):
                href = link.get_attribute("href")
                if not href:
                    continue
                full_url = href if href.startswith("http") else f"https://josephine.proebiz.com{href}"
                text_block = link.inner_text().strip()
                if any(kw in text_block.lower() for kw in KEYWORDS):
                    results.append({
                        "source": "JOSEPHINE",
                        "title": text_block.split("\n")[0][:180],
                        "raw_text": text_block,
                        "url": full_url
                    })
            browser.close()
    except Exception as e:
        print(f"Chyba JOSEPHINE: {e}")
    return results


def fetch_uvo_and_eks_tenders() -> list:
    results = []
    print("2/5 Sťahujem ÚVO a EKS...")
    cpv_codes = ["45310000-3", "45311000-0", "45312000-7", "45315000-8", "45316000-5", "45210000-2"]
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
                    href = link_el["href"]
                    full_url = href if href.startswith("http") else f"https://www.uvo.gov.sk{href}"
                    results.append({
                        "source": "ÚVO / EVO",
                        "title": cols[0][:180],
                        "raw_text": " | ".join(cols),
                        "url": full_url
                    })
    except Exception as e:
        print(f"Chyba ÚVO: {e}")

    # EKS verejné zákazky
    try:
        resp_eks = requests.get("https://www.eks.sk/VerejneZakazky", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp_eks.status_code == 200:
            soup = BeautifulSoup(resp_eks.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = a.get_text(" ", strip=True)
                if any(kw in txt.lower() for kw in KEYWORDS) and len(txt) > 15:
                    href = a["href"]
                    full_url = href if href.startswith("http") else f"https://www.eks.sk{href}"
                    results.append({
                        "source": "EKS Trhovisko",
                        "title": txt[:180],
                        "raw_text": txt,
                        "url": full_url
                    })
    except Exception as e:
        print(f"Chyba EKS: {e}")
    return results


def fetch_private_b2b_portals() -> list:
    """Sťahuje súkromné a firemné dopyty z 123dopyt, AAAdopyt a Daibau."""
    results = []
    print("3/5 Sťahujem súkromné B2B dopytové portály...")
    portals = [
        ("123dopyt.sk", "https://www.123dopyt.sk/dopyty/elektroinstalacie"),
        ("123dopyt.sk", "https://www.123dopyt.sk/dopyty/fotovoltika-a-solarna-energia"),
        ("AAAdopyt.sk", "https://www.aaadopyt.sk/dopyty/elektroinstalacie"),
        ("Daibau.sk", "https://www.daibau.sk/dopyty/elektroinstalacie")
    ]
    for source_name, url in portals:
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = a.get_text(" ", strip=True)
                if len(txt) > 20 and any(kw in txt.lower() for kw in KEYWORDS):
                    href = a["href"]
                    if href.startswith("/"):
                        base = "/".join(url.split("/")[:3])
                        full_url = base + href
                    elif href.startswith("http"):
                        full_url = href
                    else:
                        continue
                    results.append({
                        "source": source_name,
                        "title": txt[:180],
                        "raw_text": txt,
                        "url": full_url
                    })
        except Exception as e:
            print(f"Chyba {source_name}: {e}")
    return results


def fetch_construction_leads_and_gc() -> list:
    """Vyhľadáva nové stavebné zámery a generálnych dodávateľov v KE/PO kraji (subdodávky)."""
    results = []
    print("4/5 Hľadám stavebné zámery a subdodávky pre stavebné firmy (KE/PO)...")
    queries = [
        'site:enviroportal.sk "Košice" OR "Prešov" "výrobná hala" OR "bytový dom" 2026',
        '"hľadáme subdodávateľa" OR "dopyt elektroinštalácie" "Košice" OR "Prešov"',
        '"stavebné povolenie" "výrobná hala" OR "skladová hala" "Košický kraj" OR "Prešovský kraj" 2026'
    ]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for q in queries:
                search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(q)}"
                page.goto(search_url, timeout=25000)
                page.wait_for_timeout(2000)
                for res in page.query_selector_all(".result"):
                    title_el = res.query_selector(".result__a")
                    snippet_el = res.query_selector(".result__snippet")
                    if title_el:
                        title = title_el.inner_text().strip()
                        href = title_el.get_attribute("href") or ""
                        snippet = snippet_el.inner_text().strip() if snippet_el else ""
                        if href.startswith("http"):
                            results.append({
                                "source": "Stavebný zámer / GD",
                                "title": title[:180],
                                "raw_text": f"{title} - {snippet}",
                                "url": href
                            })
            browser.close()
    except Exception as e:
        print(f"Chyba pri hľadaní stavebných zámerov: {e}")
    return results


def enrich_detail_text(item: dict) -> str:
    try:
        resp = requests.get(item["url"], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            return soup.get_text(" ", strip=True)[:6500]
    except Exception:
        pass
    return item["raw_text"]


def evaluate_with_gemini(client: genai.Client, item: dict, detail_text: str) -> TenderEvaluation | None:
    system_prompt = """
    Si obchodný a technický analytik pre slovenskú elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Profil firmy HelMcore s.r.o.:
    - Oprávnenia: VTZ elektrické, odborná spôsobilosť § 23 vrátane prostredia s nebezpečenstvom výbuchu (Trieda B / Ex), registrácia v EVO.
    - Zameranie: silnoprúdové a slaboprúdové elektroinštalácie (priemysel, haly, komerčné objekty, bytové domy), výroba NN rozvádzačov na mieru, bleskozvody (vrátane HVI), fotovoltika (FTV), EPS (Aritech, Kidde), EZS (Ajax), CCTV (Dahua), štruktúrovaná kabeláž a optika, inteligentné inštalácie (Ampio, DALI).
    - Región: Košice a okruh do 100 km (Košický kraj, Prešovský kraj – Prešov, Poprad, Spišská Nová Ves, Michalovce, Humenné, Trebišov, Rožňava).
    - Minimálna hodnota zákazky: 5 000 EUR bez DPH.

    Pravidlá vyhodnotenia:
    1. Ak je miesto realizácie mimo Východného Slovenska (napr. Bratislava, Trnava, Žilina, Nitra, Banská Bystrica), nastav is_relevant = False a score = 0 (výnimka len pre veľké zákazky nad 150 000 EUR).
    2. Ak je hodnota prác zjavne pod 5 000 EUR bez DPH (napr. oprava jednej zásuvky alebo malý byt), nastav is_relevant = False.
    3. Ak ide o nový stavebný zámer (výrobná hala, bytový dom, rekonštrukcia objektu v KE/PO kraji), vyhodnoť ho ako príležitosť pre subdodávku elektro/EPS/bleskozvody pre generálneho dodávateľa stavby (is_relevant = True, score 75-90).
    """

    user_prompt = f"""
    Analyzuj tento dopyt / zákazku / stavebný zámer:
    Zdroj: {item['source']}
    Názov: {item['title']}
    URL: {item['url']}
    Text detailu:
    {detail_text}
    """

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
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
    val_str = f"{tender['estimated_value_eur']:,.0f} € bez DPH".replace(",", " ") if tender["estimated_value_eur"] > 0 else "Odhad > 5 000 €"
    msg = (
        f"🔥 *ZHODA {tender['score']} % | {tender['source']}*\n"
        f"*{tender['title']}*\n\n"
        f"📍 *Lokalita:* {tender['location']}\n"
        f"💶 *Hodnota:* {val_str}\n"
        f"⏳ *Termín:* {tender['deadline']}\n"
        f"🛠 *Rozsah:* {tender['summary']}\n"
        f"✅ *Výhoda HelMcore:* {tender['helmcore_advantage']}\n"
        f"⚠️ *Riziká:* {tender['risks']}\n\n"
        f"🔗 [Otvoriť zdroj]({tender['url']})"
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
        print(f"Chyba Telegram: {e}")


def main():
    if not GEMINI_API_KEY:
        raise RuntimeError("Chýba GEMINI_API_KEY v GitHub Secrets!")

    client = genai.Client(api_key=GEMINI_API_KEY)
    existing = load_existing_tenders()
    existing_ids = {t["id"] for t in existing}

    raw_candidates = (
        fetch_josephine_tenders()
        + fetch_uvo_and_eks_tenders()
        + fetch_private_b2b_portals()
        + fetch_construction_leads_and_gc()
    )
    print(f"5/5 Spolu nájdených {len(raw_candidates)} kandidátov zo všetkých zdrojov pred AI filtrom.")

    # Deduplikácia URL v rámci jedného behu
    unique_candidates = []
    seen_urls = set()
    for c in raw_candidates:
        if c["url"] not in seen_urls:
            seen_urls.add(c["url"])
            unique_candidates.append(c)

    new_count = 0
    for item in unique_candidates[:80]:
        tid = make_id(item["source"], item["title"], item["url"])
        if tid in existing_ids:
            continue

        detail_text = enrich_detail_text(item)
        eval_res = evaluate_with_gemini(client, item, detail_text)
        if not eval_res:
            continue

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
    print(f"Hotovo. Pridaných {new_count} nových príležitostí nad 5 000 €.")


if __name__ == "__main__":
    main()
