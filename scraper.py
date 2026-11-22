import os
import shutil
import json
import hashlib
import time
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

ELECTRO_KEYWORDS = [
    "elektroinštal", "elektromontáž", "rozvádzač", "silnoprúd", "slaboprúd",
    "bleskozvod", "uzemnen", "fotovolt", "ftv", "eps", "požiarna signalizácia",
    "ezs", "zabezpečovac", "kamerov", "cctv", "osvetlen", "dali", "kábl",
    "revízi", "opaos", "vtz", "nn prípojk", "vn ", "trafostanic", "videovrátnik",
    "štruktúrovaná kabeláž"
]

WEST_CENTRAL_CITIES = [
    "bratislava", "trnava", "trenčín", "nitra", "žilina", "banská bystrica",
    "zvolen", "martin", "prievidza", "piešťany", "komárno", "nové zámky",
    "levice", "lučenec", "rimavská sobota", "čadca", "ružomberok", "liptovský mikuláš",
    "dubnica", "považská bystrica", "senec", "pezinok", "malacky", "skalica",
    "topoľčany", "topolčany", "hlohovec", "sládkovičovo", "sliač", "voderady",
    "malinovo", "jarovce", "špačince", "petrovany"
]


class SingleEvaluation(BaseModel):
    index: int = Field(description="Poradové číslo položky zo vstupného zoznamu.")
    is_relevant: bool = Field(description="True len ak je realizácia v KE/PO kraji, ide o elektro/VTZ/slaboprúd/FTV/bleskozvody alebo veľkú stavbu pre elektro subdodávku, a hodnota je >= 5000 EUR.")
    score: int = Field(description="Skóre zhody od 0 do 100 (90-100 čisté elektro/VTZ v KE/PO, 70-89 subdodávka alebo širšia stavba s elektro časťou).")
    estimated_value_eur: float = Field(description="Odhadovaná hodnota v EUR bez DPH (0 ak sa nedá určiť).")
    location: str = Field(description="Mesto alebo okres realizácie (napr. Košice, Prešov, Trebišov, Valaliky, Poprad).")
    summary: str = Field(description="Vecné zhrnutie rozsahu elektro prác alebo stavby (max 2 vety).")
    helmcore_advantage: str = Field(description="Konkrétna výhoda HelMcore (VTZ, § 23 Ex, EVO, rozvádzače, EPS/EZS, FTV, subdodávka).")
    risks: str = Field(description="Konkrétne riziká alebo 'Bez zjavných rizík'.")
    deadline: str = Field(description="Termín ponuky z textu alebo 'Podľa portálu'.")


class BatchEvaluation(BaseModel):
    items: list[SingleEvaluation]


def sync_logo_to_docs():
    os.makedirs("docs", exist_ok=True)
    if os.path.exists("logo.png"):
        shutil.copy2("logo.png", "docs/logo.png")
        print("Logo skopírované do docs/logo.png")


def load_existing_tenders() -> list:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return [item for item in data if item.get("source") != "Daibau.sk"]
            except json.JSONDecodeError:
                return []
    return []


def save_tenders(tenders: list):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    tenders.sort(key=lambda x: (x.get("found_at", ""), x.get("score", 0)), reverse=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tenders[:300], f, ensure_ascii=False, indent=2)


def make_id(source: str, title: str, url: str) -> str:
    raw = f"{source}:{title.strip().lower()}:{url.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_likely_western_or_central(text: str) -> bool:
    t = text.lower()
    return any(city in t for city in WEST_CENTRAL_CITIES)


def detect_east_location(text: str) -> str | None:
    t = text.lower()
    mapping = {
        "košic": "Košice / okolie", "kosic": "Košice / okolie",
        "valalik": "Valaliky (KE-okolie)", "moldav": "Moldava nad Bodvou",
        "prešov": "Prešov / okolie", "presov": "Prešov / okolie",
        "poprad": "Poprad / okolie", "spišsk": "Spišská Nová Ves",
        "spissk": "Spišská Nová Ves", "michalov": "Michalovce",
        "humenn": "Humenné", "trebišov": "Trebišov", "trebisov": "Trebišov",
        "rožňav": "Rožňava", "roznava": "Rožňava", "bardejov": "Bardejov",
        "sabinov": "Sabinov", "vranov": "Vranov nad Topľou",
        "svidník": "Svidník", "svidnik": "Svidník", "kežmar": "Kežmarok",
        "kezmar": "Kežmarok", "stará ľubov": "Stará Ľubovňa",
        "stara lubov": "Stará Ľubovňa", "snin": "Snina", "gelnica": "Gelnica",
        "stropkov": "Stropkov", "krompach": "Krompachy",
        "košický kraj": "Košický kraj", "kosicky kraj": "Košický kraj",
        "prešovský kraj": "Prešovský kraj", "presovsky kraj": "Prešovský kraj"
    }
    for key, label in mapping.items():
        if key in t:
            return label
    return None


def fetch_josephine_tenders() -> list:
    results = []
    print("1/4 Sťahujem JOSEPHINE (Proebiz)...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = context.new_page()
            page.goto("https://josephine.proebiz.com/sk/public-tenders/all", timeout=45000)
            page.wait_for_selector("a[href*='/public-tender/']", timeout=15000)
            page.wait_for_timeout(2000)

            seen_hrefs = set()
            for link in page.query_selector_all("a[href*='/public-tender/']"):
                href = link.get_attribute("href")
                if not href or href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                full_url = href if href.startswith("http") else f"https://josephine.proebiz.com{href}"
                title_txt = link.inner_text().strip().split("\n")[0]
                if len(title_txt) >= 6:
                    results.append({
                        "source": "JOSEPHINE",
                        "title": title_txt[:180],
                        "raw_text": title_txt,
                        "url": full_url
                    })
            browser.close()
    except Exception as e:
        print(f"Chyba JOSEPHINE: {e}")
    print(f"   -> JOSEPHINE načítaných: {len(results)}")
    return results


def fetch_uvo_and_evo_tenders() -> list:
    results = []
    print("2/4 Sťahujem ÚVO / EVO...")
    cpv_codes = [
        "45310000-3", "45311000-0", "45312000-7",
        "45315000-8", "45316000-5", "09331200-0"
    ]
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
    print(f"   -> ÚVO / EVO nájdených: {len(results)}")
    return results


def fetch_eks_and_ezak_tenders() -> list:
    results = []
    print("3/4 Sťahujem EKS Trhovisko a E-ZAK profily...")
    try:
        resp_eks = requests.get("https://www.eks.sk/VerejneZakazky", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp_eks.status_code == 200:
            soup = BeautifulSoup(resp_eks.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = a.get_text(" ", strip=True)
                if any(kw in txt.lower() for kw in ELECTRO_KEYWORDS) and len(txt) > 15:
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

    ezak_portals = [
        ("E-ZAK TUKE", "https://zakazky.tuke.sk/vz/"),
        ("E-ZAK UPJŠ", "https://zakazky.upjs.sk/vz/"),
        ("E-ZAK UNLP", "https://zakazky.unlp.sk/vz/"),
        ("E-ZAK VVS", "https://zakazky.vodarne.eu/vz/"),
        ("E-ZAK ŽSR", "https://zakazky.zsr.sk/vz/")
    ]
    for label, url in ezak_portals:
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt = a.get_text(" ", strip=True)
                if ("/contract_display_" in href or "/vz0" in href) and len(txt) > 10:
                    base = "/".join(url.split("/")[:3])
                    full_url = href if href.startswith("http") else f"{base}{href}"
                    results.append({
                        "source": label,
                        "title": txt[:180],
                        "raw_text": f"{label}: {txt}",
                        "url": full_url
                    })
        except Exception:
            continue
    print(f"   -> EKS / E-ZAK nájdených: {len(results)}")
    return results


def fetch_slovak_b2b_and_construction() -> list:
    results = []
    print("4/4 Prehľadávam Tendernet, ProfiTender, JOSEPHINE SK a stavebné zámery...")
    queries = [
        'site:josephine.proebiz.com/sk/public-tender "Košice" OR "Prešov" OR "Poprad" "elektro" OR "rozvádzač" OR "osvetlenie"',
        'site:tendernet.sk "Košice" OR "Prešov" OR "východné Slovensko" "elektro" OR "fotovoltika" OR "rozvádzač"',
        'site:profitender.sk "Košice" OR "Prešov" "elektroinštalácie" OR "elektromontáže" OR "slaboprúd"',
        'site:cez.sk "Košice" OR "Prešov" "elektroinštalácie" OR "rozvádzač"',
        'site:enviroportal.sk "Košice" OR "Prešov" "výrobná hala" OR "bytový dom" OR "fotovoltická" 2026',
        '"hľadáme subdodávateľa" OR "dopyt elektroinštalácie" "Košice" OR "Prešov" OR "Poprad"'
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
                        if not href.startswith("http"):
                            continue
                        if "josephine.proebiz.com" in href:
                            src = "JOSEPHINE"
                        elif "tendernet.sk" in href or "profitender.sk" in href:
                            src = "B2B Tendernet/ProfiTender"
                        else:
                            src = "B2B / Subdodávka"
                        results.append({
                            "source": src,
                            "title": title[:180],
                            "raw_text": f"{title} - {snippet}",
                            "url": href
                        })
            browser.close()
    except Exception as e:
        print(f"Chyba B2B vyhľadávania: {e}")
    print(f"   -> B2B / Tendernet / Subdodávky nájdených: {len(results)}")
    return results


def enrich_detail_text(item: dict) -> str:
    try:
        resp = requests.get(item["url"], timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)[:3200]
    except Exception:
        pass
    return item["raw_text"]


def fallback_local_evaluation(batch: list[dict]) -> list[SingleEvaluation]:
    print("Používam lokálny elektro filter (bez AI)...")
    evals = []
    for idx, item in enumerate(batch):
        combined = f"{item['title']} {item['raw_text']} {item['detail']}".lower()
        loc = detect_east_location(combined)
        has_electro = any(kw in combined for kw in ELECTRO_KEYWORDS)
        if loc and has_electro:
            evals.append(SingleEvaluation(
                index=idx,
                is_relevant=True,
                score=78,
                estimated_value_eur=0.0,
                location=loc,
                summary=item["raw_text"][:260],
                helmcore_advantage="Zhoda lokality (KE/PO kraj) a kľúčových slov elektro / VTZ.",
                risks="AI vyhodnotenie bolo nedostupné – overiť rozpočet a lehotu na portáli.",
                deadline="Podľa portálu"
            ))
    return evals


def evaluate_batch_with_gemini(client: genai.Client, batch: list[dict]) -> list[SingleEvaluation]:
    system_prompt = """
    Si obchodný a technický analytik pre slovenskú elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Oprávnenia: VTZ elektrické, § 23 (vrátane Ex prostredia), EVO, výroba NN rozvádzačov, bleskozvody, FTV, EPS (Aritech, Kidde), EZS (Ajax), CCTV, DALI/Ampio.
    Región: Výhradne Východné Slovensko (Košický kraj a Prešovský kraj).
    Minimálna hodnota: 5 000 EUR bez DPH.

    Prísne pravidlá hodnotenia:
    1. Ak je realizácia mimo KE/PO kraja (západné/stredné Slovensko), nastav is_relevant = False.
    2. Ak ide len o všeobecnú stavbu (zateplenie, cesty, okná) BEZ zmienky o elektroinštalácii, rozvádzačoch, FTV, EPS alebo veľkej hale vhodnej na subdodávku, nastav is_relevant = False.
    3. Ak je hodnota zjavne pod 5 000 EUR bez DPH, nastav is_relevant = False.
    4. Vypočítaj reálne skóre (70-100) podľa zhody na portfólio HelMcore a napíš konkrétny popis pre každú položku zvlášť.
    """

    entries = []
    for idx, item in enumerate(batch):
        entries.append(f"[{idx}] ZDROJ: {item['source']} | NÁZOV: {item['title']} | TEXT: {item['detail']}")

    user_prompt = "Vyhodnoť nasledujúci zoznam položiek:\n\n" + "\n---\n".join(entries)

    for model_name in ["gemini-2.5-flash", "gemini-2.5-flash-lite"]:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_schema=BatchEvaluation,
                    temperature=0.1,
                ),
            )
            parsed = BatchEvaluation.model_validate_json(response.text)
            print(f"AI analýza úspešná cez model {model_name}.")
            return parsed.items
        except Exception as e:
            print(f"Model {model_name} nedostupný ({e}), skúšam ďalší...")
            time.sleep(2)

    return fallback_local_evaluation(batch)


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
    sync_logo_to_docs()
    if not GEMINI_API_KEY:
        print("UPOZORNENIE: Chýba GEMINI_API_KEY v GitHub Secrets!")
    client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    existing = load_existing_tenders()
    existing_ids = {t["id"] for t in existing}

    sources_lists = [
        fetch_josephine_tenders()[:25],
        fetch_uvo_and_evo_tenders()[:25],
        fetch_eks_and_ezak_tenders()[:20],
        fetch_slovak_b2b_and_construction()[:25],
    ]

    filtered_candidates = []
    seen_urls = set()
    for src_list in sources_lists:
        for c in src_list:
            if c["url"] in seen_urls:
                continue
            seen_urls.add(c["url"])
            tid = make_id(c["source"], c["title"], c["url"])
            if tid in existing_ids:
                continue
            if is_likely_western_or_central(c["title"] + " " + c["raw_text"]):
                continue
            c["id"] = tid
            filtered_candidates.append(c)

    print(f"Vybraných {len(filtered_candidates)} kandidátov na hĺbkovú analýzu.")

    to_analyze = filtered_candidates[:60]
    for item in to_analyze:
        item["detail"] = enrich_detail_text(item)

    new_count = 0
    for start in range(0, len(to_analyze), 20):
        batch = to_analyze[start:start + 20]
        evals = evaluate_batch_with_gemini(client, batch) if client else fallback_local_evaluation(batch)
        for ev in evals:
            if 0 <= ev.index < len(batch) and ev.is_relevant and ev.score >= 70:
                if ev.estimated_value_eur > 0 and ev.estimated_value_eur < MIN_VALUE_EUR:
                    continue
                item = batch[ev.index]
                record = {
                    "id": item["id"],
                    "source": item["source"],
                    "title": item["title"],
                    "url": item["url"],
                    "score": ev.score,
                    "estimated_value_eur": ev.estimated_value_eur,
                    "location": ev.location,
                    "summary": ev.summary,
                    "helmcore_advantage": ev.helmcore_advantage,
                    "risks": ev.risks,
                    "deadline": ev.deadline,
                    "found_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                }
                existing.append(record)
                existing_ids.add(item["id"])
                new_count += 1
                send_telegram_notification(record)

    save_tenders(existing)
    print(f"Hotovo. Pridaných {new_count} nových príležitostí.")


if __name__ == "__main__":
    main()
