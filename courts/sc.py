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
    """Uploads image bytes to DeathByCaptcha and returns arithmetic or numeric result."""
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

                    # ✅ CASE 1: Arithmetic like "5+3" or "9-4"
                    m = re.match(r"^(\d+)\s*([\+\-])\s*(\d+)$", decoded)
                    if m:
                        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
                        result = str(a + b if op == "+" else a - b)
                        print(f"✅ Evaluated arithmetic {a}{op}{b}={result}")
                        return result

                    # ✅ CASE 2: Already a final numeric answer
                    if decoded.isdigit():
                        print(f"✅ DBC gave numeric result directly: {decoded}")
                        return decoded

                    # ⚙️ CASE 3: Fallback — 2 digits, no operator, maybe OCR lost symbol
                    if decoded.isdigit() and len(decoded) == 2:
                        op_guess = detect_operator_from_image(img_bytes)
                        if op_guess:
                            a, b = int(decoded[0]), int(decoded[1])
                            result = str(a + b if op_guess == "+" else a - b)
                            print(f"🧠 Hybrid DBC+local solver inferred {a}{op_guess}{b}={result}")
                            return result

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


def detect_operator_from_image(img_bytes: bytes) -> str | None:
    """Detect whether + or - symbol appears between digits using local OCR and contour analysis."""
    img = Image.open(BytesIO(img_bytes)).convert("L")
    img_cv = np.array(img)
    _, thresh = cv2.threshold(img_cv, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    h, w = thresh.shape

    # Focus on middle horizontal band (operator region)
    y1, y2 = int(h * 0.35), int(h * 0.65)
    middle = thresh[y1:y2, :]

    # Run OCR just for + or -
    text = pytesseract.image_to_string(
        middle,
        config="--psm 7 -c tessedit_char_whitelist=+-"
    ).strip()
    print(f"🔍 Operator OCR result: '{text}'")

    if "+" in text:
        return "+"
    if "-" in text:
        return "-"
    return None

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
        # Enhance contrast and preserve operator visibility
        img_cv = cv2.GaussianBlur(img_cv, (3,3), 0)
        img_cv = cv2.adaptiveThreshold(
            img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 10
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
                # 🧩 Attempt operator recovery if only digits detected (like "42")
        if cleaned.isdigit() and len(cleaned) == 2:
            op = detect_operator_from_image(img_bytes)
            if op:
                a, b = int(cleaned[0]), int(cleaned[1])
                res = str(a + b if op == "+" else a - b)
                print(f"🧠 Recovered operator locally: {a}{op}{b}={res}")
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
            .replace("\/", "")
            .replace("\r\n", "")
            .replace("<\/th>", "")
            .replace("<\/div>", "")
            .replace("<\/td>", "")
            .replace("<\/center>", "")
            .replace("\/", "")
            .replace("<\/tr>", "")
            .replace("<\/tbody>", "")
            .replace("<\/table>", "")

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
    """Extracts and returns the main results table or card HTML safely."""
    soup = BeautifulSoup(html, "html.parser")

    # Find table or fallback container
    table = soup.find("table")
    if not table:
        table = soup.find("div", class_="record-card") or soup

    # Ensure links open in new tab
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
    return JSONResponse({
    "success": True,
    "data": {
        "resultsHtml": extract_results_table(html)
    }
})


@router.get("/daily_orders/diary_number")
def daily_orders_diary_number(diary_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/daily-order-diary-no/", {"diary_no": diary_no, "year": year}, "get_daily_order_diary_no")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

@router.get("/daily_orders/ROP_date")
def daily_orders_rop(from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/daily-order-rop-date/", {"from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_daily_order_rop_date")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

@router.get("/daily_orders/free_text")
def daily_orders_free_text(search_text: str, from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/free-text-orders/", {"search_text": search_text, "from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_daily_order_free_text")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

# --- Judgements ---
@router.get("/judgements/case_number")
def judgements_case_number(case_type: str, case_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/judgements-case-no/", {"case_type": case_type, "case_no": case_no, "year": year}, "get_judgements_case_no")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

@router.get("/judgements/diary_number")
def judgements_diary_number(diary_no: str, year: str):
    html = search_via_ajax(f"{SC_URL}/judgements-diary-no/", {"diary_no": diary_no, "year": year}, "get_judgements_diary_no")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

@router.get("/judgements/judge")
def judgements_by_judge(judge: str, from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/judgements-judge/", {"judge": judge, "from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_judgements_judge")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

@router.get("/judgements/judgement_date")
def judgements_by_date(from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/judgements-judgement-date/", {"from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_judgements_judgement_date")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })

@router.get("/judgements/free_text")
def judgements_free_text(search_text: str, from_date: str, to_date: str):
    html = search_via_ajax(f"{SC_URL}/free-text-judgements/", {"search_text": search_text, "from_date": format_date(from_date), "to_date": format_date(to_date)}, "get_judgements_free_text")
    return JSONResponse({
        "success": True,
        "data": {
            "resultsHtml": extract_results_table(html)
        }
    })
