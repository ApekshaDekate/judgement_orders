# -----------------------
Correct code just captcha breaker ius not giving proper answer



import re
import math
import requests
from io import BytesIO
from PIL import Image
import pytesseract
import cv2
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import io, os, datetime

# -----------------------
# CONFIG
# -----------------------
SC_URL = "https://www.sci.gov.in"
AJAX_URL = f"{SC_URL}/wp-admin/admin-ajax.php"
DBC_USERNAME = "sschitaleyserver@gmail.com"
DBC_PASSWORD = "cdAP1988"
router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ======================
# HELPERS
# ======================
def format_date(date_str: str) -> str:
    """Convert yyyy-mm-dd to dd-mm-yyyy."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
    except Exception:
        return date_str


# ===========================================================
# CAPTCHA SOLVER — now saves image + creates new session each time
# ===========================================================

def preprocess_image_for_ocr(img_bytes):
    """Enhance small symbols (+/-) for better OCR accuracy."""
    img = Image.open(io.BytesIO(img_bytes)).convert("L")
    img_cv = np.array(img, dtype=np.uint8)
    
    # Adaptive threshold (handles gray captcha backgrounds)
    img_cv = cv2.adaptiveThreshold(
        img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10
    )
    
    # Dilation to thicken small + or - symbols
    kernel = np.ones((2, 2), np.uint8)
    img_cv = cv2.dilate(img_cv, kernel, iterations=1)
    
    # Invert back (black text on white background)
    img_cv = cv2.bitwise_not(img_cv)
    
    return Image.fromarray(img_cv)

def solve_arithmetic_captcha_from_bytes(img_bytes, session=None, captcha_url=None, max_retries=5):
    """
    Solves arithmetic captchas (e.g., 6+5, 8-3) using OCR with adaptive thresholding.
    Retries OCR multiple times and optionally refetches a new captcha if all attempts fail.
    """
    try:
        # === Save captcha image for inspection ===
        folder = os.path.join(os.path.dirname(__file__), "captchas")
        os.makedirs(folder, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        img_path = os.path.join(folder, f"captcha_{timestamp}.png")
        with open(img_path, "wb") as f:
            f.write(img_bytes)
        print(f"🖼️ Captcha image saved at: {img_path}")

        # === OCR retry loop ===
        expression = None
        for attempt in range(1, max_retries + 1):
            img_proc = preprocess_image_for_ocr(img_bytes)
            configs = [
                "--psm 7 -c tessedit_char_whitelist=0123456789+-",
                "--psm 6 -c tessedit_char_whitelist=0123456789+-",
            ]

            for config in configs:
                text = pytesseract.image_to_string(img_proc, config=config).strip()
                cleaned = re.sub(r"[^0-9+\-]", "", text)
                print(f"🔍 [Attempt {attempt}] OCR Raw: '{text}'  Cleaned: '{cleaned}'")

                match = re.search(r"(\d+)\s*([\+\-])\s*(\d+)", cleaned)
                if match:
                    expression = match.groups()
                    break

            if expression:
                break
            else:
                print(f"🔁 Retry #{attempt}: Still no valid arithmetic found...")

        # === Auto-refresh if failed ===
        if not expression:
            if session and captcha_url:
                print("♻️ Fetching a new captcha image for retry...")
                resp = session.get(captcha_url)
                if resp.status_code == 200:
                    return solve_arithmetic_captcha_from_bytes(resp.content, session, captcha_url)
            raise RuntimeError(f"❌ Could not detect arithmetic captcha after {max_retries} retries.")

        # === Solve the expression ===
        a, op, b = expression
        result = str(int(a) + int(b)) if op == "+" else str(int(a) - int(b))
        print(f"✅ Captcha solved successfully: {a}{op}{b}={result}")

        # Rename image for debugging clarity
        solved_path = img_path.replace(".png", f"_{a}{op}{b}={result}.png")
        os.rename(img_path, solved_path)

        return result

    except Exception as e:
        print(f"⚠️ Captcha solver error: {e}")
        return None



# ===========================================================
# TOKEN + CAPTCHA FETCHER — now always uses new session
# ===========================================================
def fetch_tokens_and_captcha(form_url: str):
    """
    Fetch hidden tokens and captcha URL from form page.
    Always starts a new session (fresh cookies, fresh captcha).
    """
    session = requests.Session()  # 🆕 Fresh session every time
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": form_url,
        "Cache-Control": "no-cache",
    }

    resp = session.get(form_url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tokens = {
        inp.get("name"): inp.get("value", "")
        for inp in soup.find_all("input", {"type": "hidden"})
        if inp.get("name")
    }

    captcha_tag = soup.find("img", id=re.compile(r"siwp_captcha_image", re.I)) \
        or soup.select_one("div.siwp-captcha img")
    captcha_src = captcha_tag["src"] if captcha_tag and captcha_tag.get("src") else None

    return session, tokens, captcha_src, headers


# ===========================================================
# AJAX SEARCH — refreshes captcha/session for every request
# ===========================================================
def search_via_ajax(form_url: str, payload: dict, action_name: str) -> str:
    """Unified search with captcha handling."""
    session, tokens, captcha_src, headers = fetch_tokens_and_captcha(form_url)
    params = {**tokens, **payload}

    # Handle captcha (always new)
    if captcha_src:
        from urllib.parse import urljoin
        captcha_url = urljoin(form_url, captcha_src)
        # Add cache buster
        captcha_url = f"{captcha_url}?cb={datetime.datetime.now().timestamp()}"
        img_resp = session.get(captcha_url, headers=headers, timeout=20)
        img_resp.raise_for_status()
        solved_value = solve_arithmetic_captcha_from_bytes(img_resp.content)

        # Detect captcha input name dynamically
        captcha_field = next((k for k in tokens if "siwp_captcha" in k), "siwp_captcha_value")
        params[captcha_field] = solved_value

    # Final parameters
    params.update({
        "language": "en",
        "submit": "Search",
        "action": action_name,
        "es_ajax_request": "1"
    })

    r = session.get(AJAX_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


# ===========================================================
# CLEAN TABLE OUTPUT
# ===========================================================
def extract_results_table(html: str) -> str:
    """Extract result table or card from returned HTML."""
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")

    content = soup.find("div", class_="record-card") or soup.find("table") or soup
    for a in content.find_all("a", href=True):
        a["href"] = urljoin(SC_URL, a["href"])
        a["target"] = "_blank"
    for tag in content(["script", "style"]):
        tag.decompose()

    clean_html = str(content).replace("\r", "").replace("\n", "")
    return f"<div class='table-responsive mt-3'>{clean_html}</div>"


# ===========================================================
# ROUTES — UNTOUCHED
# ===========================================================
@router.get("/sc", response_class=HTMLResponse)
def sc_form(request: Request):
    return templates.TemplateResponse("sc_form.html", {"request": request})


@router.get("/judges")
def get_judges():
    r = requests.get(f"{SC_URL}/judgements-judge/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    judges = [
        {"id": opt.get("value").strip(), "name": opt.text.strip()}
        for opt in soup.select("select#judge option")
        if opt.get("value") and opt.text.strip().lower() != "--select--"
    ]
    return {"judges": judges}


@router.get("/case-details")
def get_case_details():
    r = requests.get(f"{SC_URL}/judgements-case-no/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    case_types = [
        {"id": opt.get("value").strip(), "name": opt.text.strip()}
        for opt in soup.select("select#case_type option")
        if opt.get("value") and opt.text.strip().lower() != "--select--"
    ]
    years = sorted(
        {opt.get("value").strip() for opt in soup.select("select#year option") if opt.get("value")},
        reverse=True
    )
    return {"case_types": case_types, "case_years": years}


# ======================
# DAILY ORDERS
# ======================
@router.get("/daily_orders/case_number")
def daily_orders_case_number(case_type: str, case_no: str, year: str):
    html = search_via_ajax(
        f"{SC_URL}/daily-order-case-no/",
        {"case_type": case_type, "case_no": case_no, "year": year},
        "get_daily_order_case_no"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/daily_orders/diary_number")
def daily_orders_diary_number(diary_no: str, year: str):
    html = search_via_ajax(
        f"{SC_URL}/daily-order-diary-no/",
        {"diary_no": diary_no, "year": year},
        "get_daily_order_diary_no"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/daily_orders/ROP_date")
def daily_orders_rop(from_date: str, to_date: str):
    html = search_via_ajax(
        f"{SC_URL}/daily-order-rop-date/",
        {"from_date": format_date(from_date), "to_date": format_date(to_date)},
        "get_daily_order_rop_date"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/daily_orders/free_text")
def daily_orders_free_text(search_text: str, from_date: str, to_date: str):
    html = search_via_ajax(
        f"{SC_URL}/free-text-orders/",
        {"search_text": search_text, "from_date": format_date(from_date), "to_date": format_date(to_date)},
        "get_daily_order_free_text"
    )
    return HTMLResponse(content=extract_results_table(html))


# ======================
# JUDGEMENTS
# ======================
@router.get("/judgements/case_number")
def judgements_case_number(case_type: str, case_no: str, year: str):
    html = search_via_ajax(
        f"{SC_URL}/judgements-case-no/",
        {"case_type": case_type, "case_no": case_no, "year": year},
        "get_judgements_case_no"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/judgements/diary_number")
def judgements_diary_number(diary_no: str, year: str):
    html = search_via_ajax(
        f"{SC_URL}/judgements-diary-no/",
        {"diary_no": diary_no, "year": year},
        "get_judgements_diary_no"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/judgements/judge")
def judgements_by_judge(judge: str, from_date: str, to_date: str):
    html = search_via_ajax(
        f"{SC_URL}/judgements-judge/",
        {"judge": judge, "from_date": format_date(from_date), "to_date": format_date(to_date)},
        "get_judgements_judge"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/judgements/judgement_date")
def judgements_by_date(from_date: str, to_date: str):
    html = search_via_ajax(
        f"{SC_URL}/judgements-judgement-date/",
        {"from_date": format_date(from_date), "to_date": format_date(to_date)},
        "get_judgements_judgement_date"
    )
    return HTMLResponse(content=extract_results_table(html))


@router.get("/judgements/free_text")
def judgements_free_text(search_text: str, from_date: str, to_date: str):
    html = search_via_ajax(
        f"{SC_URL}/free-text-judgements/",
        {"search_text": search_text, "from_date": format_date(from_date), "to_date": format_date(to_date)},
        "get_judgements_free_text"
    )
    return HTMLResponse(content=extract_results_table(html))



# ======================
# NEW CODE
# ======================

import re
import math
import time
import requests
from io import BytesIO
from PIL import Image
import pytesseract
import cv2
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import io, os, tempfile
from urllib.parse import unquote, urljoin

# -----------------------
# CONFIG
# -----------------------
SC_URL = "https://www.sci.gov.in"
AJAX_URL = f"{SC_URL}/wp-admin/admin-ajax.php"
DBC_USERNAME = "sschitaleyserver@gmail.com"
DBC_PASSWORD = "cdAP1988"
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ======================
# HELPERS
# ======================
def format_date(date_str: str) -> str:
    """Convert yyyy-mm-dd to dd-mm-yyyy."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
    except Exception:
        return date_str

# ======================
# DeathByCaptcha integration

def solve_captcha_dbc_from_bytes(img_bytes: bytes, max_poll_attempts: int = 8, poll_interval: int = 8) -> str | None:
    """Uploads image bytes to DeathByCaptcha and returns evaluated arithmetic result if any."""
    if not DBC_USERNAME or not DBC_PASSWORD:
        return None

    api_upload = "http://api.dbcapi.me/api/captcha"
    api_get = "http://api.dbcapi.me/api/captcha/{captcha_id}"

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(tmp_fd)
    try:
        with open(tmp_path, "wb") as f:
            f.write(img_bytes)

        files = {"captchafile": ("captcha.png", open(tmp_path, "rb"), "image/png")}
        data = {"username": DBC_USERNAME, "password": DBC_PASSWORD}
        try:
            print("🔄 Uploading captcha to DeathByCaptcha...")
            resp = requests.post(api_upload, data=data, files=files, timeout=20)
        finally:
            files["captchafile"][1].close()

        if resp.status_code != 200:
            print(f"❌ DBC upload failed: HTTP {resp.status_code} {resp.text}")
            return None

        raw = resp.text.strip()
        print(f"📨 DBC upload response: {raw}")
        if "captcha=" not in raw:
            print("❌ DBC response didn't contain captcha id")
            return None

        captcha_id = raw.split("captcha=")[1].split("&")[0]
        print(f"⏳ Polling DBC for captcha id {captcha_id} ...")
        time.sleep(poll_interval)

        for attempt in range(1, max_poll_attempts + 1):
            try:
                poll_resp = requests.get(api_get.format(captcha_id=captcha_id), timeout=15)
                poll_text = poll_resp.text.strip()
                print(f"  Poll {attempt} response: {poll_text}")
                if "text=" in poll_text:
                    text = poll_text.split("text=")[1].split("&")[0]
                    decoded = unquote(text).strip().replace(" ", "").replace("=", "")
                    print(f"🔍 DBC raw decoded text: '{decoded}'")

                    # ✅ Check if it's arithmetic like "5+3" or "9-4"
                    m = re.match(r"^(\d+)\s*([\+\-])\s*(\d+)$", decoded)
                    if m:
                        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
                        result = str(a + b if op == "+" else a - b)
                        print(f"✅ Evaluated arithmetic {a}{op}{b}={result}")
                        return result

                    # Otherwise, return numeric if already a number
                    if decoded.isdigit():
                        print(f"✅ DBC gave numeric: {decoded}")
                        return decoded
            except Exception as e:
                print(f"  Poll error: {e}")
            time.sleep(poll_interval)

        print("❌ DBC: no solution after polling attempts")
        return None
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# ======================
# Local solver (fallback)
# ======================
def solve_arithmetic_captcha_from_bytes_local(img_bytes: bytes) -> str | None:
    """Local OCR-based solver: expects a+b or a-b. Returns 'a+b' result or None."""
    try:
        folder = os.path.join(os.path.dirname(__file__), "captchas")
        os.makedirs(folder, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        raw_path = os.path.join(folder, f"captcha_{ts}.png")
        with open(raw_path, "wb") as f:
            f.write(img_bytes)

        img = Image.open(BytesIO(img_bytes)).convert("L")
        img_cv = np.array(img, dtype=np.uint8)
        img_cv = cv2.adaptiveThreshold(
            img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 9
        )
        kernel = np.ones((2, 2), np.uint8)
        img_cv = cv2.dilate(img_cv, kernel, iterations=1)
        img_cv = cv2.bitwise_not(img_cv)
        img_proc = Image.fromarray(img_cv)

        configs = [
            "--psm 7 -c tessedit_char_whitelist=0123456789+-",
            "--psm 6 -c tessedit_char_whitelist=0123456789+-",
        ]
        for cfg in configs:
            text = pytesseract.image_to_string(img_proc, config=cfg).strip()
            cleaned = re.sub(r"[^0-9+\-]", "", text)
            print(f"🔍 Local OCR raw: '{text}' cleaned: '{cleaned}'")
            m = re.search(r"(\d+)\s*([\+\-])\s*(\d+)", cleaned)
            if m:
                a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
                res = str(a + b) if op == "+" else str(a - b)
                solved_name = raw_path.replace(".png", f"_{a}{op}{b}={res}.png")
                try:
                    os.rename(raw_path, solved_name)
                except Exception:
                    pass
                print(f"✅ Local solver success: {a}{op}{b}={res}")
                return res

        print("⚠️ Local solver failed to detect arithmetic pattern")
        return None

    except Exception as e:
        print(f"⚠️ Local solver error: {e}")
        return None


# ======================
# Fetch tokens & captcha
# ======================
def fetch_tokens_and_captcha(form_url: str, session: requests.Session, headers: dict):
    resp = session.get(form_url, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tokens = {inp.get("name"): inp.get("value", "") for inp in soup.find_all("input", {"type": "hidden"}) if inp.get("name")}
    captcha_tag = soup.find("img", id=re.compile(r"siwp_captcha_image", re.I)) \
        or soup.select_one("div.siwp-captcha img") \
        or soup.find("img", {"class": re.compile(r"siwp_img|siwp-captcha", re.I)})

    captcha_src = captcha_tag["src"] if captcha_tag and captcha_tag.get("src") else None
    if captcha_src and not captcha_src.startswith("http"):
        captcha_src = urljoin(form_url, captcha_src)

    return tokens, captcha_src


# ======================
# AJAX search with captcha
# ======================
def search_via_ajax(form_url: str, payload: dict, action_name: str) -> str:
    """Performs Supreme Court AJAX search with captcha solving, always saving captcha image."""
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": form_url,
        "Cache-Control": "no-cache",
    }

    tokens, captcha_src = fetch_tokens_and_captcha(form_url, session, headers)
    params = {**tokens, **payload}

    # --- ✅ Always refresh captcha for every search ---
    if captcha_src:
        # Append a random timestamp so browser/server never cache it
        captcha_url = f"{captcha_src}?cb={int(time.time() * 1000)}" if "?" not in captcha_src else f"{captcha_src}&cb={int(time.time() * 1000)}"

        for attempt in range(5):  # Retry up to 5 times if captcha fails
            print(f"🌀 Attempt {attempt + 1} to solve CAPTCHA...")

            # === Download fresh captcha ===
            img_resp = session.get(captcha_url, headers=headers, timeout=20)
            img_bytes = img_resp.content

            # ✅ Save the captcha image for debugging
            save_folder = os.path.join(os.path.dirname(__file__), "captchas")
            os.makedirs(save_folder, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            img_path = os.path.join(save_folder, f"captcha_{ts}.png")
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            print(f"🖼️ Captcha image saved at: {img_path}")

            # === Try DeathByCaptcha first ===
            solved_value = solve_captcha_dbc_from_bytes(img_bytes)

            # === Fallback to local OCR ===
            if not solved_value or not re.match(r"^\d+$", solved_value.strip()):
                print("⚠️ DBC failed or invalid, trying local OCR...")
                solved_value = solve_arithmetic_captcha_from_bytes_local(img_bytes)

            # === Validate arithmetic format (a+b / a-b only) ===
            if not solved_value or not solved_value.strip().isdigit():
                print("❌ Invalid CAPTCHA result, retrying with new image...")
                time.sleep(3)
                continue

            print(f"✅ Final CAPTCHA result: {solved_value}")
            captcha_field = next((k for k in tokens if "siwp_captcha" in k), "siwp_captcha_value")
            params[captcha_field] = solved_value
            break
        else:
            raise RuntimeError("Captcha solver failed after retries.")

    # --- AJAX Request ---
    params.update({
        "language": "en",
        "submit": "Search",
        "action": action_name,
        "es_ajax_request": "1",
    })

    r = session.get(AJAX_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text



# ======================
# CLEAN SUPREME COURT HTML
# ======================
def clean_supreme_court_html(raw_html: str) -> str:
    """Fully clean Supreme Court HTML and return plain readable text (no HTML tags)."""
    try:
        # 1️⃣ Decode escaped sequences like \r, \n, \t, \/, etc.
        decoded = raw_html.encode("utf-8").decode("unicode_escape")
        decoded = (
            decoded.replace("\\/", "/")
            .replace('\\"', '"')
            .replace("\\r", "")
            .replace("\\n", "")
            .replace("\\t", "")
        )

        # 2️⃣ Parse HTML
        soup = BeautifulSoup(decoded, "html.parser")

        # 3️⃣ Remove unwanted tags completely
        for tag in soup(["script", "style", "thead", "tbody", "th", "div", "center", "table"]):
            tag.decompose()

        # 4️⃣ Fix relative links (if any exist)
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("./"):
                a["href"] = urljoin(SC_URL, a["href"])
            a["target"] = "_blank"

        # 5️⃣ Extract plain text
        text = soup.get_text(separator=" ", strip=True)

        # 6️⃣ Final cleanup
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"[\r\n\t]+", " ", text)
        text = text.strip()

        return text

    except Exception as e:
        print(f"⚠️ HTML cleaning error: {e}")
        return raw_html

# ======================
# Extract results table
# ======================
def extract_results_table(html: str) -> str:
    clean_html = clean_supreme_court_html(html)
    soup = BeautifulSoup(clean_html, "html.parser")
    table = soup.find("table")
    if not table:
        table = soup.find("div", class_="record-card") or soup
    for a in table.find_all("a", href=True):
        a["target"] = "_blank"
    return f"<div class='table-responsive mt-3'>{str(table)}</div>"


# ======================
# ROUTES
# ======================
@router.get("/sc", response_class=HTMLResponse)
def sc_form(request: Request):
    return templates.TemplateResponse("sc_form.html", {"request": request})

@router.get("/judges")
def get_judges():
    r = requests.get(f"{SC_URL}/judgements-judge/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    judges = [
        {"id": opt.get("value").strip(), "name": opt.text.strip()}
        for opt in soup.select("select#judge option")
        if opt.get("value") and opt.text.strip().lower() != "--select--"
    ]
    return {"judges": judges}

@router.get("/case-details")
def get_case_details():
    r = requests.get(f"{SC_URL}/judgements-case-no/", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    case_types = [
        {"id": opt.get("value").strip(), "name": opt.text.strip()}
        for opt in soup.select("select#case_type option")
        if opt.get("value") and opt.text.strip().lower() != "--select--"
    ]
    years = sorted({opt.get("value").strip() for opt in soup.select("select#year option") if opt.get("value")}, reverse=True)
    return {"case_types": case_types, "case_years": years}

# --- Daily Orders ---
@router.get("/daily_orders/case_number")
def daily_orders_case_number(case_type: str, case_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/daily-order-case-no/", {"case_type": case_type, "case_no": case_no, "year": year}, "get_daily_order_case_no")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/daily_orders/diary_number")
def daily_orders_diary_number(diary_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/daily-order-diary-no/", {"diary_no": diary_no, "year": year}, "get_daily_order_diary_no")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/daily_orders/ROP_date")
def daily_orders_rop(from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/daily-order-rop-date/", {"from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_daily_order_rop_date")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/daily_orders/free_text")
def daily_orders_free_text(search_text: str, from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/free-text-orders/", {"search_text": search_text, "from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_daily_order_free_text")
    return HTMLResponse(content=extract_results_table(html))

# --- Judgements ---
@router.get("/judgements/case_number")
def judgements_case_number(case_type: str, case_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/judgements-case-no/", {"case_type": case_type, "case_no": case_no, "year": year}, "get_judgements_case_no")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/judgements/diary_number")
def judgements_diary_number(diary_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/judgements-diary-no/", {"diary_no": diary_no, "year": year}, "get_judgements_diary_no")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/judgements/judge")
def judgements_by_judge(judge: str, from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/judgements-judge/", {"judge": judge, "from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_judgements_judge")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/judgements/judgement_date")
def judgements_by_date(from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/judgements-judgement-date/", {"from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_judgements_judgement_date")
    return HTMLResponse(content=extract_results_table(html))

@router.get("/judgements/free_text")
def judgements_free_text(search_text: str, from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/free-text-judgements/", {"search_text": search_text, "from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_judgements_free_text")
    return HTMLResponse(content=extract_results_table(html))
