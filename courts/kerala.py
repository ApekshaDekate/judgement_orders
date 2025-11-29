import os, re, base64
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# === MAIN PDF DIRECTORY (COMMON FOR ALL STATES) ===
BASE_PDF_ROOT = "/media/ibmarray2_1/airstorage/commpdf/"
STATE_FOLDER = os.path.join(BASE_PDF_ROOT, "kerala")
os.makedirs(STATE_FOLDER, exist_ok=True)


@router.get("/kerala", response_class=HTMLResponse)
async def show_kerala_form(request: Request):

    judge_url = "https://hckinfo.keralacourts.in/digicourt/Casedetailssearch/CRjudgmentsearch"
    res = requests.get(judge_url)
    soup = BeautifulSoup(res.text, "html.parser")

    judge_select = soup.find("select", {"id": "judge_code"})
    judges = []

    if judge_select:
        for option in judge_select.find_all("option"):
            code = option.get("value", "0")
            name = option.text.strip()
            if code:
                judges.append({"code": code, "name": name})

    return templates.TemplateResponse("kerala_form.html", {"request": request, "judges": judges})



@router.post("/kerala/results", response_class=HTMLResponse)
async def fetch_kerala_results(
    request: Request,
    from_date: str = Form(None),
    to_date: str = Form(None),
    judge_code: str = Form("0"),
    citationno: str = Form(None),
    citationyear: str = Form(None),
    cnt: int = Form(0),
    page_cnt: int = Form(1)
):

    BASE_URL = "https://hckinfo.keralacourts.in/digicourt/Casedetailssearch/"
    headers = {"User-Agent": "Mozilla/5.0"}

    # === Decide request type ===
    if citationno and citationyear:
        url = BASE_URL + "Citation_ajax/"
        payload = {
            "from_date": from_date or "",
            "to_date": to_date or "",
            "order_type": "undefined",
            "case_types": "Select",
            "case_nos": "",
            "case_years": "",
            "citationno": citationno,
            "citationyear": citationyear,
            "cnt": cnt,
            "page_cnt": page_cnt,
            "reportable": "undefined",
            "search_type": "1"
        }
        folder_name = f"citation_{citationyear}_{citationno}"

    else:
        url = f"{BASE_URL}CRjudgmentsearchresult_ajax/{cnt}"
        payload = {
            "from_date": from_date,
            "to_date": to_date,
            "judge_code": judge_code,
            "cnt": cnt,
            "page_cnt": page_cnt
        }
        folder_name = f"judge_{judge_code}_{from_date}_{to_date}"

    # === Create date folder ===
    today = datetime.now().strftime("%Y-%m-%d")
    DATE_FOLDER = os.path.join(STATE_FOLDER, today)
    os.makedirs(DATE_FOLDER, exist_ok=True)

    # === Create search-wise folder inside date folder ===
    folder_path = os.path.join(DATE_FOLDER, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    total_pdfs = 0

    # === PAGINATION LOOP ===
    while True:
        print(f"📄 Fetching results with cnt={cnt} ...")

        if citationno and citationyear:
            response = requests.post(url, data=payload, headers=headers, timeout=30)
        else:
            paged_url = f"{BASE_URL}CRjudgmentsearchresult_ajax/{cnt}"
            response = requests.post(paged_url, data=payload, headers=headers, timeout=30)

        if response.status_code != 200:
            print("⚠️ Page fetch failed.")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup.find_all("div", class_="box-footer"):
            tag.decompose()

        pdf_tags = []
        for a_tag in soup.find_all("a"):
            onclick = a_tag.get("onclick", "")
            if "viewordercitation" in onclick:
                match = re.search(
                    r"viewordercitation\('([^']+)','([^']+)','([^']+)','([^']+)'\)",
                    onclick
                )
                if match:
                    token, lookups, citationno, isqr = match.groups()
                    full_url = (
                        f"{BASE_URL}fileviewcitation?token={token}&lookups={lookups}"
                        f"&citationno={citationno}&isqr={isqr}"
                    )
                    pdf_tags.append((a_tag, full_url))

        print(f"🧾 Found {len(pdf_tags)} PDFs")

        # === Download PDFs ===
        for a_tag, pdf_page_url in pdf_tags:
            try:
                page_resp = requests.get(pdf_page_url, headers=headers, timeout=20)
                if page_resp.status_code != 200:
                    continue

                parsed = urlparse(pdf_page_url)
                params = parse_qs(parsed.query)
                token_b64 = params.get("token", [""])[0]
                lookups_b64 = params.get("lookups", [""])[0]

                try:
                    token_dec = base64.b64decode(token_b64).decode("utf-8")
                    lookups_dec = base64.b64decode(lookups_b64).decode("utf-8")
                    pdf_url = f"https://hckinfo.keralacourts.in/digicourt/{lookups_dec}/{token_dec}"
                except:
                    continue

                filename = os.path.basename(pdf_url)
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"

                filepath = os.path.join(folder_path, filename)
                pdf_resp = requests.get(pdf_url, timeout=20)

                if pdf_resp.status_code == 200:
                    with open(filepath, "wb") as f:
                        f.write(pdf_resp.content)

                    print(f"✅ Saved: {filepath}")
                    total_pdfs += 1

                    a_tag["href"] = filename
                    a_tag.attrs.pop("onclick", None)

            except Exception as e:
                print("❌ Error:", e)

        # === Save updated HTML ===
        html_path = os.path.join(folder_path, f"results_cnt_{cnt}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(str(soup))

        print(f"💾 Saved: {html_path}")

        if len(pdf_tags) < 20:
            break

        cnt += 20
        page_cnt += 1

    print(f"🎯 Total PDFs downloaded: {total_pdfs}")

    return templates.TemplateResponse("kerala_results.html", {
        "request": request,
        "html_table": str(soup),
        "from_date": from_date,
        "to_date": to_date,
        "citationno": citationno,
        "citationyear": citationyear,
        "judge_code": judge_code,
        "pdf_count": total_pdfs,
        "pdf_folder": folder_name
    })
