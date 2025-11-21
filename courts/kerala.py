import os, re, base64
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# === NEW BASE DIRECTORY STRUCTURE ===
BASE_PDF_DIR = os.path.join("Pdfs", "kerala")  # "pdfs" main folder, then "kerala" subfolder


@router.get("/kerala", response_class=HTMLResponse)
async def show_kerala_form(request: Request):
    """Show Kerala High Court judgment search form."""
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
async def fetch_kerala_results(request: Request,
                               from_date: str = Form(None),
                               to_date: str = Form(None),
                               judge_code: str = Form("0"),
                               citationno: str = Form(None),
                               citationyear: str = Form(None),
                               cnt: int = Form(0),
                               page_cnt: int = Form(1)):

    BASE_URL = "https://hckinfo.keralacourts.in/digicourt/Casedetailssearch/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # === Choose request type ===
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

    # === Prepare download folder ===
    folder_path = os.path.join(BASE_PDF_DIR, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    total_pdfs = 0

    # === PAGINATION LOOP ===
    while True:
        print(f"📄 Fetching results with cnt={cnt} ...")

        # Fetch the correct page
        if citationno and citationyear:
            response = requests.post(url, data=payload, headers=headers, timeout=30)
        else:
            paged_url = f"{BASE_URL}CRjudgmentsearchresult_ajax/{cnt}"
            response = requests.post(paged_url, data=payload, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"⚠️ Failed to fetch page at cnt={cnt}")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        # Remove footer boxes
        for tag in soup.find_all("div", class_="box-footer"):
            tag.decompose()

        # === Extract PDF links ===
        pdf_tags = []
        for a_tag in soup.find_all("a"):
            onclick = a_tag.get("onclick", "")
            if "viewordercitation" in onclick:
                match = re.search(
                    r"viewordercitation\('([^']+)','([^']+)','([^']+)','([^']+)'\)", onclick
                )
                if match:
                    token, lookups, citationno, isqr = match.groups()
                    full_url = (
                        f"{BASE_URL}fileviewcitation?token={token}&lookups={lookups}"
                        f"&citationno={citationno}&isqr={isqr}"
                    )
                    pdf_tags.append((a_tag, full_url))

        print(f"🧾 Found {len(pdf_tags)} PDFs on this page")

        # === Download PDFs and update links ===
        for a_tag, pdf_page_url in pdf_tags:
            try:
                page_resp = requests.get(pdf_page_url, headers=headers, timeout=20)
                if page_resp.status_code != 200:
                    print(f"⚠️ Failed to fetch {pdf_page_url}")
                    continue

                parsed = urlparse(pdf_page_url)
                params = parse_qs(parsed.query)
                token_b64 = params.get("token", [""])[0]
                lookups_b64 = params.get("lookups", [""])[0]

                try:
                    token_decoded = base64.b64decode(token_b64).decode("utf-8")
                    lookups_decoded = base64.b64decode(lookups_b64).decode("utf-8")
                    pdf_url = f"https://hckinfo.keralacourts.in/digicourt/{lookups_decoded}/{token_decoded}"
                except Exception as e:
                    print(f"⚠️ Decode failed: {e}")
                    continue

                filename = os.path.basename(pdf_url.split("?")[0])
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"

                filepath = os.path.join(folder_path, filename)
                pdf_resp = requests.get(pdf_url, headers=headers, timeout=30)

                if pdf_resp.status_code == 200 and "application/pdf" in pdf_resp.headers.get("Content-Type", ""):
                    with open(filepath, "wb") as f:
                        f.write(pdf_resp.content)
                    print(f"✅ Saved: {filepath}")
                    total_pdfs += 1

                    # ✅ Replace onclick with local href
                    a_tag["href"] = filename  # link to same folder
                    if "onclick" in a_tag.attrs:
                        del a_tag["onclick"]

                else:
                    print(f"⚠️ Invalid PDF ({pdf_resp.status_code}) -> {pdf_url}")

            except Exception as e:
                print(f"❌ Error with {pdf_page_url}: {e}")

        # === Save updated HTML after all PDFs for this page ===
        html_path = os.path.join(folder_path, f"results_cnt_{cnt}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(str(soup))
        print(f"💾 Saved result table: {html_path}")

        # === Pagination condition ===
        if len(pdf_tags) < 20:
            print("⏹ No more pages or fewer than 20 results.")
            break

        cnt += 20
        page_cnt += 1

    print(f"🎯 Finished. Total PDFs downloaded: {total_pdfs}")

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
