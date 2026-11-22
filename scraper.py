import os
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

KEYWORDS = [
    "elektroinštal", "rozvádzač", "silnoprúd", "slaboprúd", "bleskozvod",
    "fotovolt", "ftv", "eps", "požiarna signalizácia", "ezs", "zabezpeč",
    "kamerov", "cctv", "osvetlen", "dali", "kábl", "revízi", "opaos", "vtz",
    "nn prípojk", "výrobná hala", "bytový dom", "rekonštrukcia objektu", "trafostanic"
]

EAST_SK_KEYWORDS = [
    "košic", "prešov", "poprad", "spišsk", "michalov", "humenn", "trebišov",
    "rožňav", "bardejov", "sabinov", "vranov", "svidník", "kežmar", "stará ľubov",
    "snin", "medzilabor", "sobranc", "gelnica", "moldav", "valalik", "kéč",
    "stropkov", "lecoč", "krompach", "veľké kapušan", "kráľovský chlmec", "sečovc",
    "košický kraj", "prešovský kraj", "východné slovensko"
]

WEST_CENTRAL_CITIES = [
    "bratislava", "trnava", "trenčín", "nitra", "žilina", "banská bystrica",
    "zvolen", "martin", "prievidza", "piešťany", "komárno", "nové zámky",
    "levice", "lučenec", "rimavská sobota", "čadca", "ružomberok", "liptovský mikuláš",
    "dubnica", "považská bystrica", "senec", "pezinok", "malacky", "skalica",
    "topoľčany", "topolčany", "hlohovec", "sládkovičovo", "sliač", "voderady", "malinovo", "jarovce"
]


class SingleEvaluation(BaseModel):
    index: int = Field(description="Poradové číslo položky zo vstupného zoznamu.")
    is_relevant: bool = Field(description="True ak je zákazka vhodná pre HelMcore s.r.o. (Východné SK KE/PO kraj, elektro/slaboprúd/FTV/bleskozvody/subdodávka, >= 5000 EUR).")
    score: int = Field(description="Skóre zhody od 0 do 100.")
    estimated_value_eur: float = Field(description="Odhadovaná hodnota v EUR bez DPH.")
    location: str = Field(description="Mesto alebo okres realizácie (napr. Košice, Prešov, Trebišov, Valaliky).")
    summary: str = Field(description="Stručný popis rozsahu prác.")
    helmcore_advantage: str = Field(description="Výhoda pre HelMcore (VTZ, § 23, rozvádzače, EPS).")
    risks: str = Field(description="Riziká alebo 'Bez zjavných rizík'.")
    deadline: str = Field(description="Termín ponuky alebo 'Podľa portálu'.")


class BatchEvaluation(BaseModel):
    items: list[SingleEvaluation]


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


def is_likely_western_or_central(text: str) -> bool:
    t = text.lower()
    return any(city in t for city in WEST_CENTRAL_CITIES)


def detect_east_location(text: str) -> str | None:
    t = text.lower()
    mapping = {
        "košic": "Košice / okolie", "valalik": "Valaliky (KE-okolie)", "moldav": "Moldava nad Bodvou",
        "prešov": "Prešov / okolie", "poprad": "Poprad / okolie", "spišsk": "Spišská Nová Ves",
        "michalov": "Michalovce", "humenn": "Humenné", "trebišov": "Trebišov",
        "rožňav": "Rožňava", "bardejov": "Bardejov", "sabinov": "Sabinov",
        "vranov": "Vranov nad Topľou", "svidník": "Svidník", "kežmar": "Kežmarok",
        "stará ľubov": "Stará Ľubovňa", "snin": "Snina", "gelnica": "Gelnica",
        "košický kraj": "Košický kraj", "prešovský kraj": "Prešovský kraj"
    }
    for key, label in mapping.items():
        if key in t:
            return label
    return None


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
        resp = requests.get(item["url"], timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            return soup.get_text(" ", strip=True)[:2500]
    except Exception:
        pass
    return item["raw_text"]


def fallback_local_evaluation(batch: list[dict]) -> list[SingleEvaluation]:
    """Záložný filter priamo v Pythone, ak sú AI servery preťažené (503)."""
    print("Používam lokálny záchranný filter pre východné Slovensko...")
    evals = []
    for idx, item in enumerate(batch):
        combined = f"{item['title']} {item['raw_text']} {item['detail']}"
        loc = detect_east_location(combined)
        if loc:
            evals.append(SingleEvaluation(
                index=idx,
                is_relevant=True,
                score=82,
                estimated_value_eur=0.0,
                location=loc,
                summary=item["raw_text"][:260],
                helmcore_advantage="Zhoda lokality (KE/PO kraj) a odboru elektro / VTZ / rozvádzače / stavby.",
                risks="Overiť presný rozpočet a lehotu priamo v podkladoch na portáli.",
                deadline="Podľa portálu"
            ))
    return evals


def evaluate_batch_with_gemini(client: genai.Client, batch: list[dict]) -> list[SingleEvaluation]:
    system_prompt = """
    Si obchodný a technický analytik pre slovenskú elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Oprávnenia: VTZ elektrické, § 23 (vrátane Ex prostredia), EVO, výroba NN rozvádzačov, bleskozvody, FTV, EPS (Aritech, Kidde), EZS (Ajax), CCTV, DALI/Ampio.
    Región: Východné Slovensko (Košický kraj a Prešovský kraj).
    Minimálna hodnota: 5 000 EUR bez DPH.

    Pravidlá:
    - Ak je realizácia mimo KE/PO kraja (západné/stredné Slovensko), nastav is_relevant = False.
    - Ak je hodnota zjavne pod 5 000 EUR bez DPH, nastav is_relevant = False.
    - Pri relevantných zákazkách a stavebných zámeroch v KE/PO kraji nastav is_relevant = True a score 70-100.
    """

    entries = []
    for idx, item in enumerate(batch):
        entries.append(f"[{idx}] ZDROJ: {item['source']} | NÁZOV: {item['title']} | TEXT: {item['detail']}")

    user_prompt = "Vyhodnoť nasledujúci zoznam položiek:\n\n" + "\n---\n".join(entries)

    for model_name in ["gemini-3.8-flash-lite", "gemini-3.8-flash"]:
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
    client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    existing = load_existing_tenders()
    existing_ids = {t["id"] for t in existing}

    raw_candidates = (
        fetch_josephine_tenders()
        + fetch_uvo_and_eks_tenders()
        + fetch_private_b2b_portals()
        + fetch_construction_leads_and_gc()
    )

    filtered_candidates = []
    seen_urls = set()
    for c in raw_candidates:
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

    print(f"5/5 Po vyradení západného SK zostalo {len(filtered_candidates)} kandidátov.")

    to_analyze = filtered_candidates[:25]
    for item in to_analyze:
        item["detail"] = enrich_detail_text(item)

    new_count = 0
    for start in range(0, len(to_analyze), 15):
        batch = to_analyze[start:start + 15]
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
