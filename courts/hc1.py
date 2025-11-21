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
import json
from datetime import datetime
import os
import re
from urllib.parse import parse_qs, urlparse

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

PDF_CACHE = "pdf_cache"
os.makedirs(PDF_CACHE, exist_ok=True)

BASE_URL = "https://judgments.ecourts.gov.in"

SEARCH_URL = f"{BASE_URL}/pdfsearch"
session = requests.Session()

CAPTCHA_SAVE_DIR = "captchas_debug"
os.makedirs(CAPTCHA_SAVE_DIR, exist_ok=True)

def solve_captcha_blacktext(session, captcha_url, max_retries=5):
    for attempt in range(1, max_retries + 1):
        resp = session.get(captcha_url, stream=True, timeout=10)
        if resp.status_code != 200:
            continue
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        img_path = os.path.join(CAPTCHA_SAVE_DIR, f"captcha_{timestamp}.png")
        with open(img_path, "wb") as f:
            f.write(resp.content)

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
    for attempt in range(1, max_attempts + 1):
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
        except Exception as e:
            print(f"[get_valid_captcha] JSON decode failed: {e}")
            time.sleep(1)
            continue
        if result.get("captcha_status") == "Y":
            print(f"[get_valid_captcha] Captcha OK. Token={result.get('app_token')}")
            return captcha_text, result.get("app_token")
        print(f"[get_valid_captcha] Server said captcha invalid.")
        time.sleep(1)
    raise Exception("Could not solve captcha")

def parse_options(html_snippet):
    if not html_snippet:
        return []
    html = f"<select>{html_snippet}</select>"
    soup = BeautifulSoup(html, "html.parser")
    options = []
    for o in soup.find_all("option"):
        val = o.get("value", "").strip()
        txt = o.text.strip()
        if val:
            options.append({"value": val, "text": txt})
    return options

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
    if a_tag:
        href = a_tag["href"]
        qs = parse_qs(urlparse(href).query)
        app_token_1 = qs.get("app_token", [app_token])[0]
    else:
        app_token_1 = app_token

    state_select = soup.find("select", {"id": "state_code"})
    state_options = parse_options(str(state_select))
    return {"app_token": app_token_1, "dropdowns": {"state_code": state_options}}

@router.get("/select_court")
def select_court(court_code: str, app_token: str):
    data = {
        "state_code": court_code,
        "app_token": app_token,
        "ajax_req": "true",
    }
    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/get_district", data=data)
    j = resp.json()
    app_token_2 = j.get("app_token", app_token)
    benches = parse_options(j.get("dist_options", ""))
    return {"app_token": app_token_2, "dropdowns": {"dist_code": benches}}

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
    app_token_3 = j.get("app_token", app_token)
    case_types = parse_options(j.get("case_type_options", ""))
    disposal_types = parse_options(j.get("disp_nature_option", ""))
    return {"app_token": app_token_3, "dropdowns": {"case_types": case_types, "disposal_types": disposal_types}}

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
    app_token_4 = j.get("app_token", app_token)
    raw_judges = j.get("res_judge1", [])
    judges = [x.strip() for x in raw_judges if x and x.strip() and not x.startswith("[")]
    return {"app_token": app_token_4, "dropdowns": {"judges": judges}}

@router.get("/select_act")
def select_act(app_token: str):
    data = {
        "app_token": app_token,
        "ajax_req": "true"
    }
    resp = session.post(f"{SEARCH_URL}/?p=pdf_search/get_data", data=data)
    j = resp.json()
    app_token_5 = j.get("app_token", app_token)
    raw_acts = j.get("res_act1", [])
    if isinstance(raw_acts, str):
        acts = parse_options(raw_acts)
    else:
        acts = [x.strip() for x in raw_acts if x and x.strip() and not x.startswith("[")]
    return {"app_token": app_token_5, "dropdowns": {"acts": acts}}

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
        "iColumns": 2,
        "sColumns": ",",
        "iDisplayStart": iDisplayStart,
        "iDisplayLength": page_size,
        "mDataProp_0": 0,
        "sSearch_0": "",
        "bRegex_0": False,
        "bSearchable_0": True,
        "bSortable_0": True,
        "mDataProp_1": 1,
        "sSearch_1": "",
        "bRegex_1": False,
        "bSearchable_1": True,
        "bSortable_1": True,
        "sSearch": "",
        "bRegex": False,
        "iSortCol_0": 0,
        "sSortDir_0": "asc",
        "iSortingCols": 1,
        "search_txt1": "",
        "search_txt2": "",
        "search_txt3": "",
        "search_txt4": "",
        "search_txt5": "",
        "pet_res": pet_res,
        "state_code": state_code,
        "state_code_li": "",
        "dist_code": dist_code or "null",
        "case_no": case_no,
        "case_year": case_year,
        "from_date": from_date,
        "to_date": to_date,
        "judge_name": judge_name,
        "reg_year": "",
        "fulltext_case_type": fulltext_case_type,
        "int_fin_party_val": "undefined",
        "int_fin_case_val": "undefined",
        "int_fin_court_val": "undefined",
        "int_fin_decision_val": "undefined",
        "act": act,
        "sel_search_by": "undefined",
        "sections": sections,
        "judge_txt": judge_name,
        "act_txt": act,
        "section_txt": "",
        "judge_val": "",
        "act_val": "",
        "year_val": "",
        "judge_arr": "",
        "flag": "",
        "disp_nature": disp_nature,
        "search_opt": "ALL",
        "date_val":date_val,
        "fcourt_type": 2,
        "citation_yr": "",
        "citation_vol": "",
        "citation_supl": "",
        "citation_page": "",
        "case_no1": "",
        "case_year1": "",
        "pet_res1": "",
        "fulltext_case_type1": "",
        "citation_keyword": "",
        "sel_lang": "",
        "proximity": "",
        "neu_cit_year": "",
        "neu_no": "",
        "ajax_req": "true",
        "app_token": app_token,
    }
    resp = session.post("https://judgments.ecourts.gov.in/pdfsearch/?p=pdf_search/home/", data=payload)
    court_json = resp.json()
    aaData = court_json.get("aaData", [])
    new_rows = []
    pattern = r"open_pdf\('([^']*)','([^']*)','([^']*)','([^']*)'\)"
    for row in aaData:
        index = row[0]
        html = row[1]
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(onclick=True):
            onclick = el.get("onclick", "")
            match = re.search(pattern, onclick)
            if not match:
                continue
            val, citation_year, path, nc_display = match.groups()
            pdf_url = f"/get_pdf?val={val}&citation_year={citation_year}&path={path}&nc_display={nc_display}&app_token={app_token}"
            new_link = soup.new_tag("a", href=pdf_url, target="_blank")
            new_link.string = "View PDF"
            el.replace_with(new_link)
        new_rows.append([index, str(soup)])
    court_json["aaData"] = new_rows
    return court_json
@router.get("/get_pdf")
def get_pdf(val: str, citation_year: str, path: str, nc_display: str, app_token: str):
    """
    Downloads a single PDF on demand when the user clicks a link on the frontend.
    Ensures session & token are valid, retries if first attempt fails.
    """
    def try_fetch_pdf(current_token: str):
        data = {
            "val": val,
            "citation_year": citation_year,
            "lang_flg": "undefined",
            "fcourt_type": "2",
            "file_type": "undefined",
            "nc_display": nc_display,
            "ajax_req": "true",
            "app_token": current_token,
            "path": path
        }
        r = session.post(f"{SEARCH_URL}/?p=pdf_search/openpdfcaptcha", data=data)
        try:
            j = r.json()
        except Exception:
            print("[get_pdf] Non-JSON response (token may be invalid).")
            return None
        return j

    # 🔹 Step 1: Try once with provided token
    result = try_fetch_pdf(app_token)

    # 🔹 Step 2: If token expired or outputfile missing, refresh session & retry
    if not result or not result.get("outputfile"):
        print("[get_pdf] Token expired or invalid, refreshing...")
        try:
            init_resp = init_form()  # Reinitializes CAPTCHA + new app_token
            new_token = init_resp.get("app_token", app_token)
            result = try_fetch_pdf(new_token)
        except Exception as e:
            print(f"[get_pdf] Token refresh failed: {e}")
            return HTMLResponse(content="PDF request failed (session refresh error)", status_code=500)

    # 🔹 Step 3: Validate PDF response
    if not result or not result.get("outputfile"):
        print("[get_pdf] Still no outputfile after retry.")
        return HTMLResponse(content="PDF details not found on server.", status_code=404)

    # 🔹 Step 4: Download and stream PDF
    outputfile = result["outputfile"].replace("\\", "/")
    pdf_url = BASE_URL + outputfile if outputfile.startswith("/") else f"{BASE_URL}/{outputfile}"
    filename = pdf_url.split("/")[-1]
    filepath = os.path.join(PDF_CACHE, filename)

    print(f"[get_pdf] Downloading from: {pdf_url}")
    if not os.path.exists(filepath) or os.path.getsize(filepath) < 1024:
        pdf_resp = session.get(pdf_url, stream=True)
        if not pdf_resp.ok:
            return HTMLResponse(content="PDF could not be downloaded.", status_code=502)
        with open(filepath, "wb") as f:
            for chunk in pdf_resp.iter_content(chunk_size=8192):
                f.write(chunk)

    print(f"[get_pdf] Serving cached PDF: {filepath}")
    return StreamingResponse(
        open(filepath, "rb"),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={filename}"}
    )
