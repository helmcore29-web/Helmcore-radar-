import os
import shutil
import json
import hashlib
import time
import re
import urllib3
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from google import genai
from google.genai import types
from pydantic import BaseModel

# Potlačenie varovaní pri štátnych E-ZAK portáloch so špecifickými SSL certifikátmi
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- NASTAVENIA PRE HELMCORE S.R.O. ---
MIN_VALUE_EUR = 5000.0
DATA_FILE = "docs/tenders.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8"
}

# Kompletné portfólio HelMcore s.r.o. (silnoprúd, slaboprúd, FTV, optika, bezpečnosť, smart)
PRIMARY_ELECTRO = [
    "elektroinštal", "elektromontáž", "rozvádzač", "silnoprúd", "slaboprúd",
    "bleskozvod", "hvi ", "uzemnen", "fotovolt", "ftv", "solárn", "menič",
    "eps", "požiarna signalizácia", "hlasová signalizácia", "hsp",
    "cbs", "centrálny batériový", "núdzové osvetlenie",
    "ezs", "zabezpečovac", "poplachov", "kamerov", "cctv", "videovrátnik", "prístupový systém",
    "dali", "ampio", "knx", "inteligentná elektro", "smart elektro",
    "štruktúrovaná kabeláž", "šks", "optick", "optika", "metalick", "datové rozvody",
    "revízi", "opaos", "vtz elektrick", "odborná prehliadka",
    "nn prípojk", "prípojka nn", "vsd", "trafostanic", "vn prípojk", "kompenzáci",
    "meranie a regulácia", "mar ", "verejné osvetlenie", "rekonštrukcia osvetlenia",
    "led osvetlen", "nabíjacia stanica", "wallbox", "elektromobil"
]

SECONDARY_BUILDING = [
    "výrobná hala", "skladová hala", "priemyselná hala", "bytový dom",
    "rekonštrukcia objektu", "rekonštrukcia budovy", "stavebné úpravy",
    "zníženie energetickej náročnosti", "modernizácia budovy", "novostavba",
    "rekonštrukcia základnej školy", "rekonštrukcia materskej školy", "domov sociálnych služieb"
]

EXCLUDE_KEYWORDS = [
    "oprava výtlkov", "vodorovné dopravné značenie", "kosenie", "zimná údržba",
    "dodávka potravín", "stravovanie", "upratovacie služby", "kancelársky papier",
    "poistenie", "právne služby", "odvoz odpadu", "osobný automobil",
    "dodávka elektrickej energie", "združená dodávka elektriny", "dodávka plynu",
    "toner", "zdravotnícky materiál", "lieky", "strážna služba"
]

# Košice + ~100 km okruh a celý Košický & Prešovský kraj
EAST_LOCATIONS = {
    "košic": "Košice", "kosic": "Košice", "šaca": "Košice-Šaca", "saca": "Košice-Šaca",
    "valalik": "Valaliky (KE)", "kechnec": "Kechnec (KE)", "hanisk": "Haniska (KE)",
    "čaňa": "Čaňa (KE)", "cana": "Čaňa (KE)", "bidovc": "Bidovce (KE)",
    "veľká ida": "Veľká Ida (KE)", "velka ida": "Veľká Ida (KE)", "seniakovc": "Seniakovce",
    "moldav": "Moldava nad Bodvou", "jasov": "Jasov (KE)", "medzev": "Medzev (KE)",
    "slaneck": "Slanec (KE)", "družstevná": "Družstevná pri Hornáde",
    "prešov": "Prešov", "presov": "Prešov", "veľký šariš": "Veľký Šariš",
    "poprad": "Poprad", "spišsk": "Spišská Nová Ves", "spissk": "Spišská Nová Ves",
    "michalov": "Michalovce", "humenn": "Humenné", "trebišov": "Trebišov",
    "trebisov": "Trebišov", "rožňav": "Rožňava", "roznava": "Rožňava",
    "bardejov": "Bardejov", "sabinov": "Sabinov", "vranov": "Vranov nad Topľou",
    "svidník": "Svidník", "svidnik": "Svidník", "kežmar": "Kežmarok",
    "kezmar": "Kežmarok", "stará ľubov": "Stará Ľubovňa", "stara lubov": "Stará Ľubovňa",
    "snin": "Snina", "gelnica": "Gelnica", "stropkov": "Stropkov",
    "krompach": "Krompachy", "sečovc": "Sečovce", "strážsk": "Strážske",
    "veľké kapušan": "Veľké Kapušany", "kráľovský chlmec": "Kráľovský Chlmec",
    "čierna nad tisou": "Čierna nad Tisou", "sobranc": "Sobrance",
    "medzilaborc": "Medzilaborce", "lipan": "Lipany", "giraltovc": "Giraltovce",
    "hanušovc": "Hanušovce nad Topľou", "svit": "Svit", "vysoké tatry": "Vysoké Tatry",
    "levoča": "Levoča", "levoca": "Levoča", "dobšin": "Dobšiná",
    "košický kraj": "Košický kraj", "kosicky kraj": "Košický kraj",
    "prešovský kraj": "Prešovský kraj", "presovsky kraj": "Prešovský kraj",
    "tuke": "Košice (TUKE)", "upjš": "Košice (UPJŠ)", "unlp": "Košice (UNLP)",
    "východoslovensk": "Východné Slovensko", "vvs, a.s.": "Východné Slovensko (VVS)",
    "teko": "Košice (TEKO)", "dpmk": "Košice (DPMK)", "bpmk": "Košice (BPMK)"
}

WEST_CENTRAL_CITIES = [
    "bratislava", "trnava", "trenčín", "nitra", "žilina", "banská bystrica",
    "zvolen", "martin", "prievidza", "piešťany", "komárno", "nové zámky",
    "levice", "lučenec", "rimavská sobota", "čadca", "ružomberok", "liptovský mikuláš",
    "dubnica", "považská bystrica", "senec", "pezinok", "malacky", "skalica",
    "topoľčany", "topolčany", "hlohovec", "sládkovičovo", "sliač", "dunajská streda"
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


def should_skip_by_region(text: str) -> bool:
    """Vyhodí západ/stred IBA vtedy, ak v texte zároveň nie je žiadna východoslovenská lokalita."""
    if detect_east_location(text):
        return False
    t = text.lower()
    return any(city in t for city in WEST_CENTRAL_CITIES)


def extract_deadline(text: str) -> str:
    m = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*202\d(?:\s+\d{1,2}:\d{2})?)", text)
    return m.group(1) if m else "Podľa portálu"


def fetch_uvo_evo() -> list:
    results = []
    print("1/5 Sťahujem ÚVO a IS EVO (všetky relevantné CPV)...")
    cpv_codes = [
        "45310000-3",  # Elektroinštalačné práce
        "45311000-0",  # Elektroinštalačné a montážne práce (rozvádzače, káble)
        "45312000-7",  # Inštalácia požiarnopoplachových a zabezpečovacích systémov (EPS, EZS, CCTV)
        "45314000-1",  # Inštalácia telekomunikačných zariadení (optika, ŠKS)
        "45315000-8",  # Elektroinštalácia kúrenia a iných zariadení budov (MaR)
        "45316000-5",  # Inštalácia osvetľovacích a signalizačných systémov (VO, DALI, CBS)
        "45317000-2",  # Iné elektroinštalačné práce (bleskozvody, uzemnenie)
        "09331200-0",  # Solárne fotovoltické moduly (FTV)
        "45231400-9",  # Výstavba elektrického vedenia (NN prípojky)
        "31210000-1",  # Elektrické rozvodné a riadiace zariadenia (výroba rozvádzačov)
        "50711000-2",  # Opravy a údržba elektrických rozvodov budov (revízie, OPAOS)
    ]
    seen_urls = set()
    for cpv in cpv_codes:
        try:
            url = f"https://www.uvo.gov.sk/vyhladavanie/vyhladavanie-zakaziek?cpv={cpv}"
            resp = requests.get(url, timeout=20, headers=HEADERS)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for row in soup.select("table tbody tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                link_el = row.find("a", href=True)
                if len(cols) >= 2 and link_el:
                    href = link_el["href"]
                    full_url = href if href.startswith("http") else f"https://www.uvo.gov.sk{href}"
                    if full_url in seen_urls:
                        continue
                    joined = " | ".join(cols)
                    if should_skip_by_region(joined):
                        continue
                    seen_urls.add(full_url)
                    src = "IS EVO" if "EVO" in joined.upper() else "ÚVO Vestník"
                    results.append({
                        "source": src,
                        "title": cols[0][:180],
                        "raw_text": joined[:500],
                        "url": full_url
                    })
        except Exception as e:
            print(f"   Chyba ÚVO CPV {cpv}: {e}")
    print(f"   -> ÚVO / IS EVO (po regionálnom predfiltri): {len(results)}")
    return results


def fetch_eks() -> list:
    results = []
    print("2/5 Sťahujem EKS Trhovisko...")
    try:
        resp = requests.get("https://www.eks.sk/VerejneZakazky", timeout=20, headers=HEADERS)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = a.get_text(" ", strip=True)
                href = a["href"]
                if len(txt) > 12 and ("Zakazka" in href or "Detail" in href or any(k in txt.lower() for k in PRIMARY_ELECTRO)):
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
    print("3/5 Sťahujem východoslovenské E-ZAK profily (Mesto KE, KSK, PSK, TUKE, UPJŠ, UNLP, VVS, ŽSR)...")
    portals = [
        ("E-ZAK Mesto Košice", "https://zakazky.kosice.sk/vz/"),
        ("E-ZAK KSK (Košický kraj)", "https://zakazky.vucke.sk/vz/"),
        ("E-ZAK PSK (Prešovský kraj)", "https://zakazky.po-kraj.sk/vz/"),
        ("E-ZAK Mesto Prešov", "https://zakazky.presov.sk/vz/"),
        ("E-ZAK TUKE", "https://zakazky.tuke.sk/vz/"),
        ("E-ZAK UPJŠ", "https://zakazky.upjs.sk/vz/"),
        ("E-ZAK UNLP Košice", "https://zakazky.unlp.sk/vz/"),
        ("E-ZAK VVS", "https://zakazky.vodarne.eu/vz/"),
        ("E-ZAK ŽSR", "https://zakazky.zsr.sk/vz/")
    ]
    seen = set()
    for label, url in portals:
        try:
            resp = requests.get(url, timeout=18, headers=HEADERS, verify=False)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            base = "/".join(url.split("/")[:3])
            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt = a.get_text(" ", strip=True)
                if ("contract_display" in href or "/vz0" in href or "zakazka" in href.lower()) and len(txt) > 10:
                    full_url = href if href.startswith("http") else (f"{base}{href}" if href.startswith("/") else f"{url.rstrip('/')}/{href}")
                    if full_url in seen:
                        continue
                    seen.add(full_url)
                    loc_hint = "Košice / Východné Slovensko" if "ŽSR" not in label else ""
                    results.append({
                        "source": label,
                        "title": txt[:180],
                        "raw_text": f"{label} {loc_hint}: {txt}",
                        "url": full_url
                    })
        except Exception as e:
            print(f"   Upozornenie {label}: {e}")
    print(f"   -> E-ZAK východ: {len(results)}")
    return results


def fetch_dynamic_portals_playwright() -> list:
    """Načíta JavaScriptom renderované portály (JOSEPHINE, eZakazky) priamo cez Chromium."""
    results = []
    print("4/5 Sťahujem JOSEPHINE (Proebiz) a eZakazky cez prehliadač Playwright...")
    seen = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=HEADERS["User-Agent"], ignore_https_errors=True)
            page = context.new_page()

            # 1. JOSEPHINE SK verejné tendre
            try:
                page.goto("https://josephine.proebiz.com/sk/public-tenders/all", timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                for a in page.query_selector_all("a[href]"):
                    href = a.get_attribute("href") or ""
                    txt = a.inner_text().strip()
                    if ("tender" in href or "/sk/" in href) and any(ch.isdigit() for ch in href) and len(txt) > 8:
                        full_url = href if href.startswith("http") else f"https://josephine.proebiz.com{href}"
                        if full_url in seen:
                            continue
                        seen.add(full_url)
                        row_txt = a.evaluate("el => (el.closest('tr, li, div.row, article') || el).innerText") or txt
                        if not should_skip_by_region(row_txt):
                            results.append({
                                "source": "JOSEPHINE",
                                "title": txt[:180],
                                "raw_text": " ".join(row_txt.split())[:450],
                                "url": full_url
                            })
            except Exception as e:
                print(f"   Upozornenie JOSEPHINE Playwright: {e}")

            # 2. eZakazky.sk
            try:
                page.goto("https://www.ezakazky.sk/", timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                for a in page.query_selector_all("a[href]"):
                    href = a.get_attribute("href") or ""
                    txt = a.inner_text().strip()
                    if len(txt) > 12 and ("zakazk" in href.lower() or "detail" in href.lower() or "tender" in href.lower()):
                        full_url = href if href.startswith("http") else f"https://www.ezakazky.sk/{href.lstrip('/')}"
                        if full_url in seen:
                            continue
                        seen.add(full_url)
                        if not should_skip_by_region(txt):
                            results.append({
                                "source": "eZakazky.sk",
                                "title": txt[:180],
                                "raw_text": txt,
                                "url": full_url
                            })
            except Exception as e:
                print(f"   Upozornenie eZakazky: {e}")

            browser.close()
    except Exception as e:
        print(f"   Chyba Playwright: {e}")

    print(f"   -> JOSEPHINE + eZakazky: {len(results)}")
    return results


def enrich_detail_text(item: dict) -> str:
    try:
        resp = requests.get(item["url"], timeout=12, headers=HEADERS, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)[:3500]
    except Exception:
        pass
    return item["raw_text"]


def smart_local_evaluation(batch: list[dict]) -> list[SingleEvaluation]:
    print("Vyhodnocujem cez rozšírený lokálny technický filter HelMcore...")
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

        score = 92 if has_primary else 75
        adv = (
            "Priama elektro/slaboprúd/FTV zákazka v KE/PO kraji – § 23, výroba rozvádzačov, bleskozvody, EPS/EZS/optika."
            if has_primary else
            "Stavebný projekt v KE/PO kraji – vhodná B2B príležitosť na subdodávku elektroinštalácie, rozvádzačov a EPS/EZS."
        )
        evals.append(SingleEvaluation(
            index=idx,
            is_relevant=True,
            score=score,
            estimated_value_eur=0.0,
            location=loc,
            summary=item["raw_text"][:280],
            helmcore_advantage=adv,
            risks="Overiť presný výkaz výmer, podmienky účasti a lehotu priamo v súťažných podkladoch.",
            deadline=extract_deadline(full_txt)
        ))
    return evals


def evaluate_batch_with_gemini(client: genai.Client, batch: list[dict]) -> list[SingleEvaluation]:
    system_prompt = """
    Si obchodný a technický analytik pre elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Odbornosti a oprávnenia: § 23 Vyhl. 508/2009 Z.z., VTZ elektrické, registrácia EVO, silnoprúdové elektroinštalácie,
    NN prípojky, výroba NN rozvádzačov, bleskozvody (vrátane HVI), fotovoltika (FTV), inteligentné inštalácie (AMPIO, DALI),
    optické siete a zváranie optiky, štruktúrovaná kabeláž (ŠKS), kamerové systémy (CCTV), videovrátniky, EZS,
    elektrická požiarna signalizácia (EPS), hlasová signalizácia požiaru (HSP), centrálne batériové systémy (CBS) a verejné osvetlenie.

    Cieľový región: Výhradne Východné Slovensko (Košice + okruh 100 km, celý Košický a Prešovský kraj).
    Minimálna hodnota: 5 000 EUR bez DPH.

    Pravidlá hodnotenia:
    1. Ak je miesto realizácie mimo Košického a Prešovského kraja, nastav is_relevant = False (pozor: obstarávateľ môže mať sídlo v Bratislave, rozhoduje MIESTO REALIZÁCIE).
    2. Ak ide len o nákup elektrickej energie (komodity), čisté asfaltovanie bez osvetlenia/prekládok NN alebo bežné tovary, nastav is_relevant = False.
    3. Priame zákazky na elektro, rozvádzače, prípojky, bleskozvody, FTV, optiku, EPS/HSP/CBS/EZS/CCTV alebo osvetlenie v KE/PO kraji hodnoť skóre 85-98.
    4. Väčšie stavby, haly a rekonštrukcie budov v KE/PO kraji (kde bude potrebná subdodávka elektra/slaboprúdu) hodnoť skóre 72-84.
    """

    entries = [f"[{idx}] ZDROJ: {item['source']} | NÁZOV: {item['title']} | TEXT: {item['detail']}" for idx, item in enumerate(batch)]
    user_prompt = "Vyhodnoť nasledujúce zákazky pre HelMcore s.r.o.:\n\n" + "\n---\n".join(entries)

    # Kaskáda reálnych modelov pri preťažení (503)
    models_to_try = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash"
    ]

    for attempt, model_name in enumerate(models_to_try):
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
            wait_sec = 10 * (attempt + 1)
            print(f"Pokus {attempt + 1}/5 ({model_name}) zlyhal: {e} -> čakám {wait_sec}s")
            time.sleep(wait_sec)

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

    # Zber zo všetkých portálov bez predčasného odrezania CPV kategórií
    sources_lists = [
        fetch_uvo_evo()[:60],
        fetch_ezak_east()[:50],
        fetch_dynamic_portals_playwright()[:40],
        fetch_eks()[:25],
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
            if should_skip_by_region(c["title"] + " " + c["raw_text"]):
                continue
            c["id"] = tid
            filtered_candidates.append(c)

    print(f"5/5 Vybraných {len(filtered_candidates)} nových kandidátov na hĺbkovú kontrolu.")

    to_analyze = filtered_candidates[:80]
    for item in to_analyze:
        item["detail"] = enrich_detail_text(item)

    new_count = 0
    for start in range(0, len(to_analyze), 20):
        batch = to_analyze[start:start + 20]
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
