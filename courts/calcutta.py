from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import requests, ssl, time, cv2, numpy as np
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib.parse import urljoin
import pytesseract
import certifi

app = FastAPI()

# ---------- SCRAPER CONFIG ----------
BASE_URL = "https://www.calcuttahighcourt.gov.in"
DATA_URL = f"{BASE_URL}/highcourt_order_search"   # first page (has captcha + hidden fields)
SEARCH_URL = f"{BASE_URL}/order_judgment_search"  # form submit url

# ---- SSL adapter (fallback for legacy handshake) ----
class LegacyAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        self.poolmanager = PoolManager(*args, ssl_context=ctx, **kwargs)

session = requests.Session()
session.mount("https://", LegacyAdapter())

# --- CAPTCHA solver ---
def solve_captcha_from_bytes(img_bytes: bytes) -> str:
    img = Image.open(BytesIO(img_bytes))
    img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    kernel = np.ones((2, 2), np.uint8)
    clean = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    clean = cv2.dilate(clean, kernel, iterations=1)
    captcha_text = pytesseract.image_to_string(
        clean,
        config=(
            "--psm 8 --oem 3 "
            "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        )
    )
    return captcha_text.strip().replace(" ", "").replace("\n", "")

# --- Main fetch with retry ---
def fetch_with_captcha(order_establishment, order_casetype, order_reg_no, order_year, max_attempts=5):

    try:
        r = session.get(DATA_URL, timeout=30, verify=True)
    except Exception:
        # fallback if verify fails
        r = session.get(DATA_URL, timeout=30, verify=False)

    soup = BeautifulSoup(r.text, "html.parser")

    # hidden token if present
    token_tag = soup.find("meta", attrs={"name": "_token"})
    _token = token_tag["content"] if token_tag else ""
    print("token1:", _token)

    # locate captcha image
    captcha_img_tag = soup.select_one("div.captcha img")
    if not captcha_img_tag:
        return None
    captcha_url = urljoin(BASE_URL, captcha_img_tag["src"])

    for attempt in range(1, max_attempts + 1):
        captcha_bytes = session.get(captcha_url, timeout=30, verify=False).content
        captcha_code = solve_captcha_from_bytes(captcha_bytes)

        if not captcha_code:
            time.sleep(1)
            continue

        print(f"Attempt {attempt}: predicted CAPTCHA: {captcha_code}")

        payload = {
            "_token": _token,
            "order_establishment": order_establishment,
            "order_casetype": order_casetype,
            "order_reg_no": order_reg_no,
            "order_year": order_year,
            "captcha": captcha_code
        }

        resp = session.post(SEARCH_URL, data=payload, timeout=30, verify=False)

        ctype = resp.headers.get("Content-Type", "").lower()
        if "image" in ctype:
            print("❌ Server returned an image (probably new captcha). Retrying…")
            time.sleep(2)
            continue

        html = resp.text
        if 'id="error_captcha"' in html or 'Incorrect captcha' in html:
            print("❌ Incorrect captcha detected. Retrying…")
            time.sleep(2)
            continue

        try:
            j = resp.json()
            print("✅ Captcha accepted. Got JSON keys:", j.keys())
            return {"type": "json", "data": j}
        except Exception:
            print("✅ Captcha accepted. Got HTML")
            return {"type": "html", "data": html}

    print("❌ All captcha attempts failed.")
    return None

# ---------- FASTAPI ENDPOINTS ----------
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("calcutta_form.html", {"request": request})

@app.post("/calcutta")
async def calcutta_search(
    order_establishment: str = Form(...),
    order_casetype: str = Form(...),
    order_reg_no: str = Form(...),
    order_year: str = Form(...)
):
    data = fetch_with_captcha(order_establishment, order_casetype, order_reg_no, order_year)
    if data is None:
        return JSONResponse({"error": "All captcha attempts failed"}, status_code=400)
    return JSONResponse(data)

@app.get("/case_types")
def get_case_types(est: str):
    url = f"{BASE_URL}/case_type_list_controller"

    # Step 1: Load main page to get CSRF token + cookies
    r = session.get(DATA_URL, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    token_tag = soup.find("meta", attrs={"name": "_token"})
    _token = token_tag["content"] if token_tag else ""

    print("token:", _token)

    # Step 2: Build payload with token + est
    payload = {
        "est": est,
        "_token": _token
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    # Step 3: Post request with session + token
    r2 = session.post(url, data=payload, headers=headers, timeout=30)

    # Step 4: Parse response <option> tags
    soup2 = BeautifulSoup(r2.text, "html.parser")
    options = []
    for opt in soup2.find_all("option"):
        val = opt.get("value")
        text = opt.text.strip()
        if val:
            options.append({"value": val, "text": text})

    return {"case_types": options}
