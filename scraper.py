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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- NASTAVENIA PRE HELMCORE S.R.O. (VERZIA V3) ---
MIN_VALUE_EUR = 5000.0
DATA_FILE = "docs/tenders.json"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "sk-SK,sk;q=0.9,en;q=0.8"
}

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
    "výrobná hala", "skladová hala", "priemyselná hala", "logistik", "bytový dom",
    "rekonštrukcia objektu", "rekonštrukcia budovy", "stavebné úpravy", "zateplenie",
    "zníženie energetickej náročnosti", "modernizácia budovy", "novostavba", "nadstavba",
    "rekonštrukcia základnej školy", "rekonštrukcia materskej školy", "domov sociálnych služieb",
    "zmluva o dielo"
]

EXCLUDE_KEYWORDS = [
    "oprava výtlkov", "vodorovné dopravné značenie", "kosenie", "zimná údržba",
    "dodávka potravín", "stravovanie", "upratovacie služby", "kancelársky papier",
    "poistenie", "právne služby", "odvoz odpadu", "osobný automobil",
    "dodávka elektrickej energie", "združená dodávka elektriny", "dodávka plynu",
    "toner", "zdravotnícky materiál", "lieky", "strážna služba", "autobus"
]

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
        json.dump(tenders[:350], f, ensure_ascii=False, indent=2)


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
    if detect_east_location(text):
        return False
    t = text.lower()
    return any(city in t for city in WEST_CENTRAL_CITIES)


def parse_eur_amount(text: str) -> float:
    m = re.search(r"(\d[\d\s\xa0]{1,12}(?:,\d{1,2})?)\s*€", text)
    if not m:
        return 0.0
    cleaned = m.group(1).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def extract_deadline(text: str) -> str:
    m = re.search(r"(\d{1,2}\.\s*\d{1,2}\.\s*202\d(?:\s+\d{1,2}:\d{2})?)", text)
    return m.group(1) if m else "Otvorené / Podľa portálu"


def fetch_uvo_evo() -> list:
    results = []
    print("1/5 Sťahujem ÚVO a IS EVO (11 CPV pre elektro/slaboprúd/FTV)...")
    cpv_codes = [
        "45310000-3", "45311000-0", "45312000-7", "45314000-1",
        "45315000-8", "45316000-5", "45317000-2", "09331200-0",
        "45231400-9", "31210000-1", "50711000-2"
    ]
    seen_urls = set()
    session = requests.Session()
    session.headers.update(HEADERS)
    for cpv in cpv_codes:
        try:
            url = f"https://www.uvo.gov.sk/vyhladavanie/vyhladavanie-zakaziek?cpv={cpv}"
            resp = session.get(url, timeout=16)
            time.sleep(0.4)
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
        except Exception:
            continue
    print(f"   -> ÚVO / IS EVO: {len(results)}")
    return results


def fetch_dynamic_portals_playwright() -> list:
    results = []
    print("2/5 Sťahujem JOSEPHINE, eZakazky a Tendernet cez prehliadač...")
    seen = set()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=HEADERS["User-Agent"], ignore_https_errors=True)
            page = context.new_page()

            try:
                page.goto("https://josephine.proebiz.com/sk/public-tenders/all", timeout=25000, wait_until="domcontentloaded")
                page.wait_for_timeout(3500)
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
                print(f"   Upozornenie JOSEPHINE: {e}")

            try:
                page.goto("https://www.ezakazky.sk/", timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
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
            except Exception:
                pass

            try:
                page.goto("https://www.tendernet.sk/zakazky", timeout=18000, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                for a in page.query_selector_all("a[href]"):
                    href = a.get_attribute("href") or ""
                    txt = a.inner_text().strip()
                    if len(txt) > 12 and ("zakazk" in href.lower() or "tender" in href.lower()):
                        full_url = href if href.startswith("http") else f"https://www.tendernet.sk/{href.lstrip('/')}"
                        if full_url in seen:
                            continue
                        seen.add(full_url)
                        if not should_skip_by_region(txt):
                            results.append({
                                "source": "Tendernet",
                                "title": txt[:180],
                                "raw_text": txt,
                                "url": full_url
                            })
            except Exception:
                pass

            browser.close()
    except Exception as e:
        print(f"   Chyba Playwright: {e}")

    print(f"   -> JOSEPHINE + eZakazky + Tendernet: {len(results)}")
    return results


def fetch_crz_b2b_subcontracts() -> list:
    results = []
    print("3/5 Sťahujem CRZ (stavebné firmy s novými zmluvami v KE/PO pre subdodávky)...")
    search_terms = [
        "elektroinštalácia Košice",
        "elektroinštalácia Prešov",
        "rekonštrukcia Košice Zmluva o dielo",
        "rekonštrukcia Prešov Zmluva o dielo",
        "fotovoltika Košice",
        "bleskozvod Košice"
    ]
    seen = set()
    for term in search_terms:
        try:
            url = f"https://www.crz.gov.sk/2171273-sk/centralny-register-zmluv/?art_zs2=&art_predmet={requests.utils.quote(term)}&art_ico=&art_suma_zmluva_od=15000"
            resp = requests.get(url, timeout=16, headers=HEADERS, verify=False)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for row in soup.select("table tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                link_el = row.find("a", href=True)
                if len(cols) >= 4 and link_el:
                    href = link_el["href"]
                    if "/zmluva/" not in href:
                        continue
                    full_url = href if href.startswith("http") else f"https://www.crz.gov.sk{href}"
                    if full_url in seen:
                        continue
                    row_joined = " | ".join(cols)
                    if should_skip_by_region(row_joined):
                        continue
                    amt = parse_eur_amount(row_joined)
                    if 0 < amt < MIN_VALUE_EUR:
                        continue
                    seen.add(full_url)
                    subject = link_el.get_text(" ", strip=True)[:140]
                    results.append({
                        "source": "CRZ – Subdodávka B2B",
                        "title": f"[SUBDODÁVKA] {subject}",
                        "raw_text": f"Čerstvá zmluva (víťazná stavebná firma na oslovenie): {row_joined[:450]}",
                        "url": full_url
                    })
        except Exception:
            continue
    print(f"   -> CRZ B2B stavebné príležitosti: {len(results)}")
    return results


def fetch_enviroportal_investments() -> list:
    results = []
    print("4/5 Sťahujem Enviroportál (nové súkromné haly, FTV a stavby na Východe)...")
    seen = set()
    urls = [
        "https://www.enviroportal.sk/eia/detail-zoznam?kraj=8",
        "https://www.enviroportal.sk/eia/detail-zoznam?kraj=7",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=16, headers=HEADERS, verify=False)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                txt = a.get_text(" ", strip=True)
                if "/eia/detail/" in href and len(txt) > 10:
                    low = txt.lower()
                    if any(k in low for k in ["hala", "park", "fotovolt", "slnečn", "obytn", "polyfunkčn", "priemyseln", "centrum", "sklad", "výrob"]):
                        full_url = href if href.startswith("http") else f"https://www.enviroportal.sk{href}"
                        if full_url in seen:
                            continue
                        seen.add(full_url)
                        kraj_lbl = "Košický kraj" if "kraj=8" in url else "Prešovský kraj"
                        results.append({
                            "source": "Súkromný investor (EIA)",
                            "title": f"[INVESTOR] {txt[:160]}",
                            "raw_text": f"Pripravovaná súkromná investícia ({kraj_lbl}) – osloviť investora/generálneho dodávateľa: {txt}",
                            "url": full_url
                        })
        except Exception:
            continue
    print(f"   -> Súkromné investície (Enviroportál): {len(results)}")
    return results


def fetch_eks() -> list:
    results = []
    print("5/5 Sťahujem EKS Trhovisko...")
    try:
        resp = requests.get("https://www.eks.sk/VerejneZakazky", timeout=16, headers=HEADERS)
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
    except Exception:
        pass
    print(f"   -> EKS Trhovisko: {len(results)}")
    return results


def enrich_detail_text(item: dict) -> str:
    try:
        resp = requests.get(item["url"], timeout=7, headers=HEADERS, verify=False)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(" ", strip=True)[:2800]
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
        is_investor = "investor" in item["source"].lower() or "crz" in item["source"].lower()

        if not has_primary and not has_secondary and not is_investor:
            continue

        est_val = parse_eur_amount(f"{item['raw_text']} {item['detail']}")
        score = 92 if has_primary else 78
        adv = (
            "Priama elektro/slaboprúd/FTV zákazka v KE/PO kraji – § 23, výroba rozvádzačov, bleskozvody, EPS/EZS/optika."
            if has_primary else
            "B2B subdodávka pre stavebnú firmu / súkromného investora v KE/PO kraji – osloviť s ponukou bez DPH na elektro, rozvádzače a slaboprúd."
        )
        evals.append(SingleEvaluation(
            index=idx,
            is_relevant=True,
            score=score,
            estimated_value_eur=est_val,
            location=loc,
            summary=item["raw_text"][:280],
            helmcore_advantage=adv,
            risks="Pri tendri skontrolovať lehotu; pri CRZ/EIA priamo kontaktovať víťaznú stavebnú firmu / investora s B2B ponukou.",
            deadline=extract_deadline(full_txt)
        ))
    return evals


def evaluate_batch_with_gemini(client: genai.Client, batch: list[dict]) -> list[SingleEvaluation]:
    system_prompt = """
    Si obchodný a technický analytik pre elektromontážnu firmu HelMcore s.r.o. (sídlo Košice, IČO: 57475393, platca DPH).
    Odbornosti: § 23 Vyhl. 508/2009 Z.z., VTZ elektrické, EVO, silnoprúdové elektroinštalácie, NN prípojky,
    výroba NN rozvádzačov, bleskozvody (vrátane HVI), fotovoltika (FTV), smart inštalácie (AMPIO, DALI),
    optika a zváranie optiky, ŠKS, CCTV, videovrátniky, EZS, EPS, HSP, CBS a verejné osvetlenie.

    Cieľový región: Výhradne Východné Slovensko (Košice + okruh 100 km, celý Košický a Prešovský kraj).
    Minimálna hodnota: 5 000 EUR bez DPH.

    Pravidlá hodnotenia:
    1. Ak je miesto realizácie mimo Košického a Prešovského kraja, nastav is_relevant = False (rozhoduje miesto stavby, nie sídlo firmy).
    2. Ak ide len o nákup elektriny ako komodity, bežné tovary alebo opravy ciest bez osvetlenia/NN prekládok, nastav is_relevant = False.
    3. Priame verejné zákazky na elektro, rozvádzače, prípojky, bleskozvody, FTV, optiku, EPS/HSP/CBS/EZS/CCTV v KE/PO kraji hodnoť skóre 86-98.
    4. Záznamy zo zdroja "CRZ – Subdodávka B2B" a "Súkromný investor (EIA)" alebo veľké stavby budov/hál v KE/PO kraji hodnoť skóre 74-85 a v summary uveď názov víťaznej stavebnej firmy alebo investora na oslovenie.
    """

    entries = [f"[{idx}] ZDROJ: {item['source']} | NÁZOV: {item['title']} | TEXT: {item['detail']}" for idx, item in enumerate(batch)]
    user_prompt = "Vyhodnoť nasledujúce príležitosti pre HelMcore s.r.o.:\n\n" + "\n---\n".join(entries)

    models_to_try = [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview"
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
            err_str = str(e)
            print(f"Pokus {attempt + 1}/{len(models_to_try)} ({model_name}) zlyhal: {err_str[:120]}")
            if "503" in err_str or "429" in err_str:
                time.sleep(4)

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
        f"🛠 *Rozsah / Kontakt:* {tender['summary']}\n"
        f"✅ *Výhoda HelMcore:* {tender['helmcore_advantage']}\n\n"
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
    print("=== HELMCORE RADAR V3 (BEZ E-ZAK / GEMINI 3.5) ===")
    sync_logo_to_docs()
    client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
    existing = load_existing_tenders()
    existing_ids = {t["id"] for t in existing}

    sources_lists = [
        fetch_uvo_evo()[:40],
        fetch_dynamic_portals_playwright()[:35],
        fetch_crz_b2b_subcontracts()[:25],
        fetch_enviroportal_investments()[:15],
        fetch_eks()[:20],
    ]

    filtered_candidates = []
    seen_urls = set()
    for src_list in sources_lists:
        for c in src_list:
            if c["url"] in seen_urls:
                continue
            seen_urls.add(c["url"])
            tid =MakeId = make_id(c["source"], c["title"], c["url"])
            if tid in existing_ids:
                continue
            if should_skip_by_region(c["title"] + " " + c["raw_text"]):
                continue
            c["id"] = tid
            filtered_candidates.append(c)

    print(f"Vybraných {len(filtered_candidates)} nových kandidátov na hĺbkovú kontrolu.")

    to_analyze = filtered_candidates[:50]
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
