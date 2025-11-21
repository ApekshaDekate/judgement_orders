from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import pytesseract
from PIL import Image
from io import BytesIO
import cv2
import numpy as np
import time
import os
import re
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------
# Setup
# ---------------------------------------------------------
router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

BASE_URL = "https://judgments.ecourts.gov.in"
SEARCH_URL = f"{BASE_URL}/pdfsearch"
PDF_CACHE = "pdf_cache"
os.makedirs(PDF_CACHE, exist_ok=True)

CAPTCHA_SAVE_DIR = "captchas_debug"
os.makedirs(CAPTCHA_SAVE_DIR, exist_ok=True)

session = requests.Session()

# ---------------------------------------------------------
# CAPTCHA Handling
# ---------------------------------------------------------
def solve_captcha_blacktext(session, captcha_url, max_retries=5):
    for _ in range(max_retries):
        resp = session.get(captcha_url, stream=True, timeout=10)
        if resp.status_code != 200:
            continue

        img = Image.open(BytesIO(resp.content)).convert("L")
        img_cv = np.array(img)
        thresh = cv2.adaptiveThreshold(
            img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 31, 15,
        )
        denoised = cv2.medianBlur(thresh, 3)

        config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        text = pytesseract.image_to_string(denoised, config=config).strip().replace(" ", "")
        if len(text) > 6:
            text = text[:6]
        if len(text) == 6:
            return text
    return ""


def get_valid_captcha(session, captcha_url, max_attempts=20):
    for attempt in range(max_attempts):
        captcha_text = solve_captcha_blacktext(session, captcha_url)
        if not captcha_text:
            time.sleep(1)
            continue
        resp = session.post(
            f"{SEARCH_URL}/?p=pdf_search/checkCaptcha",
            data={"captcha": captcha_text, "ajax_req": "True", "search_opt": "ALL"}
        )
        try:
            result = resp.json()
        except Exception:
            time.sleep(1)
            continue

        if result.get("captcha_status") == "Y":
            print(f"[get_valid_captcha] ✅ Captcha OK — Token={result.get('app_token')}")
            return captcha_text, result.get("app_token")
    raise Exception("Could not solve captcha")

# ---------------------------------------------------------
# Utilities
# ---------------------------------------------------------
def parse_options(html_snippet):
    if not html_snippet:
        return []
    html = f"<select>{html_snippet}</select>"
    soup = BeautifulSoup(html, "html.parser")
    return [{"value": o.get("value", "").strip(), "text": o.text.strip()}
            for o in soup.find_all("option") if o.get("value")]

# ---------------------------------------------------------
# Routes
# ---------------------------------------------------------
@router.get("/hc", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("highcourt_form.html", {"request": request})


@router.get("/init")
def init_form():
    resp = session.get(SEARCH_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    captcha_tag = soup.find("img", {"id": "captcha_image"})
    captcha_url = BASE_URL + captcha_tag["src"]

    captcha_text, app_token = get_valid_captcha(session, captcha_url)

    params = {
        "p": "pdf_search/home",
        "captcha": captcha_text,
        "search_opt": "ALL",
        "fcourt_type": "2",
        "app_token": app_token,
    }
    search_resp = session.get(SEARCH_URL, params=params)
    soup = BeautifulSoup(search_resp.text, "html.parser")

    a_tag = soup.find("a", href=lambda h: h and "app_token=" in h)
    app_token_final = parse_qs(urlparse(a_tag["href"]).query).get("app_token", [app_token])[0] if a_tag else app_token

    state_select = soup.find("select", {"id": "state_code"})
    state_options = parse_options(str(state_select))
    return {"app_token": app_token_final, "dropdowns": {"state_code": state_options}}


@router.get("/select_court")
def select_court(court_code: str, app_token: str):
    data = {"state_code": court_code, "app_token": app_token, "ajax_req": "true"}
    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/get_district", data=data)
    j = resp.json()
    benches = parse_options(j.get("dist_options", ""))
    return {"app_token": j.get("app_token", app_token), "dropdowns": {"dist_code": benches}}


@router.get("/select_bench")
def select_bench(court_code: str, bench_code: str, app_token: str):
    data = {
        "state_code": court_code,
        "dist_code": bench_code,
        "fcourt_type": "2",
        "app_token": app_token,
        "ajax_req": "true"
    }
    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/getCaseType", data=data)
    j = resp.json()
    return {
        "app_token": j.get("app_token", app_token),
        "dropdowns": {
            "case_types": parse_options(j.get("case_type_options", "")),
            "disposal_types": parse_options(j.get("disp_nature_option", ""))
        }
    }


@router.get("/select_judge")
def select_judge(court_code: str, bench_code: str, app_token: str):
    data = {
        "state_code": court_code,
        "dist_code": bench_code,
        "fcourt_type": "2",
        "app_token": app_token,
        "ajax_req": "true"
    }
    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/get_judge_name", data=data)
    j = resp.json()
    judges = [x.strip() for x in j.get("res_judge1", []) if x.strip() and not x.startswith("[")]
    return {"app_token": j.get("app_token", app_token), "dropdowns": {"judges": judges}}


@router.get("/select_act")
def select_act(app_token: str):
    data = {"app_token": app_token, "ajax_req": "true"}
    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/get_data", data=data)
    j = resp.json()
    app_token_5 = j.get("app_token", app_token)
    raw_acts = j.get("res_act1", [])
    if isinstance(raw_acts, str):
        acts = parse_options(raw_acts)
    else:
        acts = [x.strip() for x in raw_acts if x and x.strip() and not x.startswith("[")]
    return {"app_token": app_token_5, "dropdowns": {"acts": acts}}

# ---------------------------------------------------------
# SEARCH CASES
# ---------------------------------------------------------
@router.post("/search_cases")
def search_cases(
    state_code: str = Form(""),
    dist_code: str = Form(""),
    fulltext_case_type: str = Form(""),
    judge_name: str = Form(""),
    act: str = Form(""),
    sections: str = Form(""),
    from_date: str = Form(""),
    to_date: str = Form(""),
    disp_nature: str = Form(""),
    app_token: str = Form(...),
    page: int = Form(1),
    page_size: int = Form(100),
    s_echo: int = Form(1),
    case_year: str = Form(""),
    case_no: str = Form(""),
    pet_res: str = Form(""),
    date_val: str = Form("ALL")
):
    iDisplayStart = (page - 1) * page_size
    payload = {
        "sEcho": s_echo,
        "iDisplayStart": iDisplayStart,
        "iDisplayLength": page_size,
        "state_code": state_code,
        "dist_code": dist_code or "null",
        "case_no": case_no,
        "case_year": case_year,
        "from_date": from_date,
        "to_date": to_date,
        "judge_name": judge_name,
        "fulltext_case_type": fulltext_case_type,
        "act": act,
        "disp_nature": disp_nature,
        "pet_res": pet_res,
        "date_val": date_val,
        "fcourt_type": 2,
        "ajax_req": "true",
        "app_token": app_token,
    }

    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/home/", data=payload)
    court_json = resp.json()

    # Fix PDF links
    pattern = r"open_pdf\('([^']*)','([^']*)','([^']*)','([^']*)'\)"
    new_rows = []
    for row in court_json.get("aaData", []):
        index = row[0]
        soup = BeautifulSoup(row[1], "html.parser")
        for el in soup.find_all(onclick=True):
            m = re.search(pattern, el["onclick"])
            if not m:
                continue
            val, citation_year, path, nc_display = m.groups()
            redirect_url = f"/get_pdf?val={val}&citation_year={citation_year}&path={path}&nc_display={nc_display}&app_token={app_token}"
            el.replace_with(BeautifulSoup(f'<a href="{redirect_url}" target="_blank" class="pdf-link text-primary">📄 View PDF</a>', "html.parser"))
        new_rows.append([index, str(soup)])
    court_json["aaData"] = new_rows
    return court_json

# ---------------------------------------------------------
# FETCH PDF (robust logic from repo)
# ---------------------------------------------------------
def fetch_pdf_from_fragment(path, app_token, row_pos=0):
    pdf_link_url = f"{BASE_URL}/pdfsearch/?p=pdf_search/openpdfcaptcha"
    pdf_link_payload = {
        "val": row_pos,
        "lang_flg": "undefined",
        "path": path,
        "fcourt_type": "2",
        "file_type": "undefined",
        "nc_display": "undefined",
        "ajax_req": "true",
        "app_token": app_token,
    }

    resp = session.post(pdf_link_url, data=pdf_link_payload, verify=False, timeout=30)
    j = resp.json()
    if "outputfile" not in j:
        raise Exception(f"PDF link failed: {j}")

    pdf_url = f"{BASE_URL}{j['outputfile']}"
    filename = os.path.basename(pdf_url)
    filepath = os.path.join(PDF_CACHE, filename)

    if os.path.exists(filepath):
        return filepath

    pdf_resp = session.get(pdf_url, stream=True, verify=False)
    with open(filepath, "wb") as f:
        for chunk in pdf_resp.iter_content(8192):
            f.write(chunk)

    return filepath

@router.get("/get_pdf")
def get_pdf(val: str, citation_year: str, path: str, nc_display: str, app_token: str):
    try:
        filepath = fetch_pdf_from_fragment(path, app_token, row_pos=int(val))
        if not os.path.exists(filepath):
            raise FileNotFoundError("PDF not saved locally")

        return StreamingResponse(
            open(filepath, "rb"),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename={os.path.basename(filepath)}"
            },
        )
    except Exception as e:
        message = f"❌ Could not fetch PDF: {e}"
        return HTMLResponse(content=message, status_code=500)
