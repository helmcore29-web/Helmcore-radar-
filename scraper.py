import os
import shutil
import json
import hashlib
import time
import re
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

PRIMARY_ELECTRO = [
    "elektroinštal", "elektromontáž", "rozvádzač", "silnoprúd", "slaboprúd",
    "bleskozvod", "uzemnen", "fotovolt", "ftv", "eps", "požiarna signalizácia",
    "ezs", "zabezpečovac", "kamerov", "cctv", "dali", "štruktúrovaná kabeláž",
    "revízi", "opaos", "vtz", "nn prípojk", "trafostanic", "videovrátnik",
    "meranie a regulácia", "mar ", "verejné osvetlenie", "rekonštrukcia osvetlenia"
]

SECONDARY_BUILDING = [
    "výrobná hala", "skladová hala", "bytový dom", "rekonštrukcia objektu",
    "zníženie energetickej náročnosti", "modernizácia budovy", "novostavba"
]

EXCLUDE_KEYWORDS = [
    "oprava výtlkov", "vodorovné dopravné značenie", "kosenie", "zimná údržba",
    "dodávka potravín", "stravovanie", "upratovacie služby", "kancelársky papier",
    "poistenie", "právne služby", "odvoz odpadu", "osobný automobil"
]

EAST_LOCATIONS = {
    "košic": "Košice", "kosic": "Košice", "valalik": "Valaliky (KE)",
    "moldav": "Moldava nad Bodvou", "čaňa": "Čaňa (KE)", "cana": "Čaňa (KE)",
    "prešov": "Prešov", "presov": "Prešov", "poprad": "Poprad",
    "spišsk": "Spišská Nová Ves", "spissk": "Spišská Nová Ves",
    "michalov": "Michalovce", "humenn": "Humenné", "trebišov": "Trebišov",
    "trebisov": "Trebišov", "rožňav": "Rožňava", "roznava": "Rožňava",
    "bardejov": "Bardejov", "sabinov": "Sabinov", "vranov": "Vranov nad Topľou",
    "svidník": "Svidník", "svidnik": "Svidník", "kežmar": "Kežmarok",
    "kezmar": "Kežmarok", "stará ľubov": "Stará Ľubovňa",
    "stara lubov": "Stará Ľubovňa", "snin": "Snina", "gelnica": "Gelnica",
    "stropkov": "Stropkov", "krompach": "Krompachy", "sečovc": "Sečovce",
    "strážsk": "Strážske", "veľké kapušan": "Veľké Kapušany", "kráľovský chlmec": "Kráľovský Chlmec",
    "sobranc": "Sobrance", "medzilaborc": "Medzilaborce", "lipan": "Lipany",
    "svit": "Svit", "vysoké tatry": "Vysoké Tatry", "levoča": "Levoča", "levoca": "Levoča",
    "dobšin": "Dobšiná", "košický kraj": "Košický kraj", "kosicky kraj": "Košický kraj",
    "prešovský kraj": "Prešovský kraj", "presovsky kraj": "Prešovský kraj",
    "tuke": "Košice (TUKE)", "upjš": "Košice (UPJŠ)", "unlp": "Košice (UNLP)",
    "východoslovensk": "Východné Slovensko", "teko": "Košice (TEKO)"
}

WEST_CENTRAL_CITIES = [
    "bratislava", "trnava", "trenčín", "nitra", "žilina", "banská bystrica",
    "zvolen", "martin", "prievidza", "piešťany", "komárno", "nové zámky",
    "levice", "lučenec", "rimavská sobota", "čadca", "ružomberok", "liptovský mikuláš",
    "dubnica", "považská bystrica", "senec", "pezinok", "malacky", "skalica",
    "topoľčany", "topolčany", "hlohovec", "sládkovičovo", "sliač"
]


class SingleEvaluation(BaseModel):
    index: int
    is_relevant: bool
    score: int
    estimated_value_eur: float
    location: str
    summary: str
    helmcore_advantage: str
    risks: str
    deadline: str


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


def detect_east_location(text: str) -> str | None:
    t = text.lower()
    for key, label in EAST_LOCATIONS.items():
        if key in t:
            return label
    return None


def is_western_or_central(text: str) -> bool:
    t = text.lower()
    return any(city in t for city in WEST_CENTRAL_CITIES)


def extract_deadline(text: str) -> str:
    m = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*202\d(?:\s+\d{1,2}:\d{2})?)", text)
    return m.group(1) if m else "Podľa portálu"


def fetch_josephine() -> list:
    results = []
    print("1/6 Sťahujem JOSEPHINE (Proebiz)...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page.goto("https://josephine.proebiz.com/sk/public-tenders/all", timeout=45000, wait_until="networkidle")
            page.wait_for_timeout(4000)

            seen = set()
            for link in page.query_selector_all("a"):
                href = link.get_attribute("href") or ""
                if "public-tender" not in href or "public-tenders" in href:
                    continue
                full_url = href if href.startswith("http") else f"https://josephine.proebiz.com{href}"
                if full_url in seen:
                    continue
                seen.add(full_url)
                row_text = link.inner_text().strip()
                title = row_text.split("\n")[0][:180]
                if len(title) > 6:
                    results.append({
                        "source": "JOSEPHINE",
                        "title": title,
                        "raw_text": row_text[:400],
                        "url": full_url
                    })
            browser.close()
    except Exception as e:
        print(f"   Chyba JOSEPHINE: {e}")
    print(f"   -> JOSEPHINE: {len(results)}")
    return results


def fetch_uvo_evo() -> list:
    results = []
    print("2/6 Sťahujem ÚVO a IS EVO...")
    cpv_codes = [
        "45310000-3", "45311000-0", "45312000-7",
        "45315000-8", "45316000-5", "09331200-0", "45231400-9"
    ]
    for cpv in cpv_codes:
        try:
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
                    src = "IS EVO" if "EVO" in " ".join(cols).upper() else "ÚVO Vestník"
                    results.append({
                        "source": src,
                        "title": cols[0][:180],
                        "raw_text": " | ".join(cols),
                        "url": full_url
                    })
        except Exception:
            continue
    print(f"   -> ÚVO / IS EVO: {len(results)}")
    return results


def fetch_eks() -> list:
    results = []
    print("3/6 Sťahujem EKS Trhovisko...")
    try:
        resp = requests.get("https://www.eks.sk/VerejneZakazky", timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = a.get_text(" ", strip=True)
                href = a["href"]
                if len(txt) > 15 and ("Zakazka" in href or "Detail" in href or any(k in txt.lower() for k in PRIMARY_ELECTRO)):
                    full_url = href if href.startswith("http") else f"https://www.eks.sk{href}"
                    results.append({
                        "source": "EKS Trhovisko",
                        "title": txt[:180],
                        "raw_text": txt,
                        "url": full_url
                    })
    except Exception as e:
        print(f"   Chyba EKS: {e}")
    print(f"   -> EKS Trhovisko: {len(results)}")
    return results


def fetch_ezak_east() -> list:
    results = []
    print("4/6 Sťahujem E-ZAK profily (TUKE, UPJŠ, UNLP, VVS, ŽSR)...")
    portals = [
        ("E-ZAK TUKE", "https://zakazky.tuke.sk/vz/"),
        ("E-ZAK UPJŠ", "https://zakazky.upjs.sk/vz/"),
        ("E-ZAK UNLP", "https://zakazky.unlp.sk/vz/"),
        ("E-ZAK VVS", "https://zakazky.vodarne.eu/vz/"),
        ("E-ZAK ŽSR", "https://zakazky.zsr.sk/vz/")
    ]
    for label, url in portals:
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
                        "raw_text": f"{label} Košice: {txt}",
                        "url": full_url
                    })
        except Exception:
            continue
    print(f"   -> E-ZAK východ: {len(results)}")
    return results


def fetch_free_portals_search() -> list:
    results = []
    print("5/6 Prehľadávam CEZ, eZakazky, JOSEPHINE SK a Enviroportál...")
    queries = [
        ('JOSEPHINE', 'site:josephine.proebiz.com ("Košice" OR "Prešov" OR "Poprad" OR "Michalovce" OR "Humenné") ("elektro" OR "rozvádzač" OR "osvetlenie" OR "bleskozvod" OR "fotovolt")'),
        ('CEZ Portál', 'site:cez.sk ("Košice" OR "Prešov" OR "Poprad" OR "Košický" OR "Prešovský") ("elektroinštal" OR "rozvádzač" OR "osvetlenie" OR "revízi")'),
        ('eZakazky.sk', 'site:ezakazky.sk ("Košice" OR "Prešov" OR "Poprad" OR "Michalovce" OR "Trebišov") ("elektro" OR "osvetlenie" OR "fotovolt" OR "rekonštrukcia")'),
        ('IS EBE', 'site:isebe.sk ("Košice" OR "Prešov" OR "Poprad") ("elektro" OR "osvetlenie" OR "rozvádzač")'),
        ('Enviroportál EIA', 'site:enviroportal.sk ("Košice" OR "Prešov" OR "Valaliky" OR "Poprad") ("výrobná hala" OR "skladová hala" OR "fotovoltická" OR "bytový dom") 2026')
    ]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            for src_name, q in queries:
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
                                "source": src_name,
                                "title": title[:180],
                                "raw_text": f"{title} - {snippet}",
                                "url": href
                            })
            browser.close()
    except Exception as e:
        print(f"   Chyba vyhľadávania free portálov: {e}")
    print(f"   -> CEZ / eZakazky / Enviroportál: {len(results)}")
    return results


def enrich_detail_text(item: dict) -> str:
    try:
        resp = requests.get(item["url"], timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)[:3500]
    except Exception:
        pass
    return item["raw_text"]


def smart_local_evaluation(batch: list[dict]) -> list[SingleEvaluation]:
    print("Vyhodnocujem cez lokálny technický filter HelMcore...")
    evals = []
    for idx, item in enumerate(batch):
        full_txt = f"{item['title']} {item['raw_text']} {item['detail']}".lower()
        if any(ex in full_txt for ex in EXCLUDE_KEYWORDS):
            continue
        loc = detect_east_location(full_txt)
        if not loc:
            continue

        has_primary = any(kw in full_txt for kw in PRIMARY_ELECTRO)
        has_secondary = any(kw in full_txt for kw in SECONDARY_BUILDING)
        if not has_primary and not has_secondary:
            continue

        score = 92 if has_primary else 74
        adv = (
            "Priama elektro/VTZ zákazka v KE/PO kraji – oprávnenie § 23, VTZ, vlastná výroba rozvádzačov."
            if has_primary else
            "Väčší stavebný projekt v KE/PO kraji – príležitosť na elektro/slaboprúd subdodávku."
        )
        evals.append(SingleEvaluation(
            index=idx,
            is_relevant=True,
            score=score,
            estimated_value_eur=0.0,
            location=loc,
            summary=item["raw_text"][:280],
            helmcore_advantage=adv,
            risks="Overiť presný výkaz výmer a lehotu priamo v podkladoch na portáli.",
            deadline=extract_deadline(full_txt)
        ))
    return evals


def evaluate_batch_with_gemini(client: genai.Client, batch: list[dict]) -> list[SingleEvaluation]:
    system_prompt = """
    Si obchodný a technický analytik pre slovenskú elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Oprávnenia: VTZ elektrické, § 23 (vrátane Ex prostredia), EVO, výroba NN rozvádzačov, bleskozvody, FTV, EPS (Aritech, Kidde), EZS (Ajax), CCTV, DALI/Ampio.
    Región: Výhradne Východné Slovensko (Košický kraj a Prešovský kraj).
    Minimálna hodnota: 5 000 EUR bez DPH.

    Pravidlá hodnotenia:
    1. Ak je realizácia mimo KE/PO kraja, nastav is_relevant = False.
    2. Ak ide len o asfaltovanie ciest, chodníkov alebo parkovísk bez elektroinštalácií, nastav is_relevant = False.
    3. Čisté elektro/VTZ/rozvádzače/FTV/EPS/osvetlenie v KE/PO kraji hodnoť skóre 85-98.
    4. Veľké rekonštrukcie budov a hál v KE/PO kraji vhodné na subdodávku elektra hodnoť skóre 70-84.
    """

    entries = [f"[{idx}] ZDROJ: {item['source']} | NÁZOV: {item['title']} | TEXT: {item['detail']}" for idx, item in enumerate(batch)]
    user_prompt = "Vyhodnoť nasledujúce položky:\n\n" + "\n---\n".join(entries)

    for model_name in ["gemini-3.5-flash", "gemini-3.5-flash-lite"]:
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
            print(f"AI analýza úspešná cez {model_name}.")
            return parsed.items
        except Exception as e:
            print(f"Model {model_name} nedostupný ({e})")
            time.sleep(2)

    return smart_local_evaluation(batch)


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
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=15
        )
    except Exception:
        pass


def main():
    sync_logo_to_docs()
    client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    existing = load_existing_tenders()
    existing_ids = {t["id"] for t in existing}

    sources_lists = [
        fetch_josephine()[:30],
        fetch_uvo_evo()[:30],
        fetch_eks()[:20],
        fetch_ezak_east()[:25],
        fetch_free_portals_search()[:30],
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
            if is_western_or_central(c["title"] + " " + c["raw_text"]):
                continue
            c["id"] = tid
            filtered_candidates.append(c)

    print(f"6/6 Vybraných {len(filtered_candidates)} kandidátov zo všetkých 10+ free portálov.")

    to_analyze = filtered_candidates[:75]
    for item in to_analyze:
        item["detail"] = enrich_detail_text(item)

    new_count = 0
    for start in range(0, len(to_analyze), 25):
        batch = to_analyze[start:start + 25]
        evals = evaluate_batch_with_gemini(client, batch) if client else smart_local_evaluation(batch)
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
