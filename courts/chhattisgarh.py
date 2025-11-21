import os
import re
import time
import cv2
import pytesseract
import numpy as np
import requests
from io import BytesIO
from PIL import Image
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, Form
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()
templates = Jinja2Templates(directory="templates")

BASE_URL = "https://highcourt.cg.gov.in/hcbspjudgement"
DATA_URL = f"{BASE_URL}/oj_search.php"


# ============================================================
# 🔹 Utility: Solve captcha image
# ============================================================
def solve_captcha_from_bytes(img_bytes):
    img = np.array(Image.open(BytesIO(img_bytes)).convert("RGB"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.medianBlur(gray, 3)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    processed = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    text = pytesseract.image_to_string(
        processed, config="-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-*/ --psm 7"
    )
    return "".join(ch for ch in text if ch.isalnum() or ch in "+-*/").strip()


# ============================================================
# 🔹 Extract only the result table
# ============================================================
def extract_results_table(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "example"}) or soup.find("table")
    if not table:
        return "<p style='color:red;'>⚠️ No results found.</p>"

    for tag in table.find_all(["script", "style"]):
        tag.decompose()
    return str(table)


# ============================================================
# 🔹 Fetch judgments (with captcha)
# ============================================================
def fetch_chhattisgarh_judgments(**kwargs):
    session = requests.Session()
    resp = session.get(DATA_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    captcha_img = soup.find("img", {"id": "captcha"})
    if not captcha_img:
        return "<p>⚠️ Captcha image not found.</p>"

    captcha_url = f"{BASE_URL}/{captcha_img['src']}"
    img_resp = session.get(captcha_url)
    captcha_text = solve_captcha_from_bytes(img_resp.content)
    print("🧩 Captcha solved as:", captcha_text)

    data = {
        "afrm": kwargs.get("afrm", ""),
        "afry": kwargs.get("afry", ""),
        "judge_name": kwargs.get("judge_name", "-1"),
        "citation_year": kwargs.get("citation_year", ""),
        "citation_number": kwargs.get("citation_number", ""),
        "case_type": kwargs.get("case_type", ""),
        "case_no": kwargs.get("case_number", ""),
        "case_yr": kwargs.get("case_year", ""),
        "party_name": kwargs.get("party_name", ""),
        "fcase_type": kwargs.get("fcase_type", ""),
        "fcase_no": kwargs.get("fcase_number", ""),
        "fcase_yr": kwargs.get("fcase_year", ""),
        "captcha": captcha_text,
        "button1": "Submit",
        "example_length": "100",
    }

    response = session.post(DATA_URL, data=data)
    print("🔎 Search status:", response.status_code)
    return response.text


# ============================================================
# 🔹 Download all PDFs with retry + subfolder naming
# ============================================================
def download_all_pdfs(result_html, base_dir="chhattisgarh_pdfs", folder_name="results"):
    from bs4 import BeautifulSoup

    # Create structured folder
    folder_name = re.sub(r"[^a-zA-Z0-9_]", "_", folder_name)
    date_folder = time.strftime("%Y-%m-%d")
    full_path = os.path.join(base_dir, date_folder, folder_name)
    os.makedirs(full_path, exist_ok=True)

    session = requests.Session()
    soup = BeautifulSoup(result_html, "html.parser")
    pdf_links = [a["id"] for a in soup.find_all("a", class_="get_data_btn") if a.get("id")]
    print(f"🔗 Found {len(pdf_links)} PDF links in {folder_name}")

    for i, pdf_id in enumerate(pdf_links, 1):
        for attempt in range(3):
            try:
                print(f"📥 [{i}/{len(pdf_links)}] Downloading {pdf_id} (Attempt {attempt+1})")
                token_resp = session.post(f"{BASE_URL}/viewpdftoken.php", timeout=10)
                token_match = re.search(r'"([a-f0-9]+)"', token_resp.text)
                if not token_match:
                    raise ValueError("Token missing")
                token = token_match.group(1)

                pdf_url = f"{BASE_URL}/viewpdf.php?csrf_token={token}&pdf_link={pdf_id}"
                pdf_resp = session.get(pdf_url, stream=True, timeout=20)

                if pdf_resp.status_code == 200 and "application/pdf" in pdf_resp.headers.get("content-type", ""):
                    filename = pdf_id.replace("/", "_") + ".pdf"
                    filepath = os.path.join(full_path, filename)
                    with open(filepath, "wb") as f:
                        for chunk in pdf_resp.iter_content(1024):
                            f.write(chunk)
                    print(f"✅ Saved: {filepath}")
                    break
                else:
                    raise Exception(f"Bad response: {pdf_resp.status_code}")

            except Exception as e:
                print(f"⚠️ Error downloading {pdf_id}: {e}")
                if attempt == 2:
                    print(f"❌ Giving up on {pdf_id}")
                time.sleep(2)

    print(f"📁 All PDFs saved in: {full_path}")


# ============================================================
# 🔹 FASTAPI ROUTES
# ============================================================
@router.get("/chhattisgarh", response_class=HTMLResponse)
def chhattisgarh_form(request: Request):
    return templates.TemplateResponse("chhattisgarh_form.html", {"request": request})


@router.post("/chhattisgarh/search", response_class=HTMLResponse)
def chhattisgarh_search(
    request: Request,
    search_type: str = Form(...),
    judge_name: str = Form("-1"),
    case_type: str = Form(""),
    case_number: str = Form(""),
    case_year: str = Form(""),
    fcase_type: str = Form(""),
    fcase_number: str = Form(""),
    fcase_year: str = Form(""),
    afrm: str = Form(""),
    afry: str = Form(""),
    citation_year: str = Form(""),
    citation_number: str = Form(""),
    party_name: str = Form("")
):
    print("🔥 POST /chhattisgarh/search called")

    # Step 1️⃣ Fetch judgments
    html_content = fetch_chhattisgarh_judgments(
        search_type=search_type,
        judge_name=judge_name,
        case_type=case_type,
        case_number=case_number,
        case_year=case_year,
        fcase_type=fcase_type,
        fcase_number=fcase_number,
        fcase_year=fcase_year,
        afrm=afrm,
        afry=afry,
        citation_year=citation_year,
        citation_number=citation_number,
        party_name=party_name
    )

    # Step 2️⃣ Extract result table
    clean_table = extract_results_table(html_content)

    # Step 3️⃣ Start PDF downloads
    folder_name = party_name.strip() or f"{case_type}_{case_number}_{case_year}"
    folder_name = folder_name.replace(" ", "_") or "unknown"
    try:
        download_all_pdfs(html_content, folder_name=folder_name)
    except Exception as e:
        print(f"⚠️ Error downloading PDFs: {e}")

    # Save a copy of the HTML table to local disk
    html_save_path = f"chhattisgarh_pdfs/{folder_name}_results.html"
    with open(html_save_path, "w", encoding="utf-8") as f:
        f.write(clean_table)
    print(f"💾 Saved HTML results table at: {html_save_path}")


    # Step 4️⃣ Render frontend result page
    return templates.TemplateResponse(
        "chhattisgarh_results.html",
        {"request": request, "result_html": clean_table, "folder_name": folder_name}
    )
