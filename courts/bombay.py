import os
import re
import requests
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import httpx

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def convert_date_format(date_str: str) -> str:
    """Convert YYYY-MM-DD → DD-MM-YYYY"""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")


def download_pdf(pdf_url, pdf_path):
    try:
        if not os.path.exists(pdf_path):
            r = requests.get(pdf_url, timeout=10)
            with open(pdf_path, 'wb') as f:
                f.write(r.content)
    except Exception as e:
        print(f"Failed to download PDF: {pdf_url}", e)


def sanitize_filename(name: str) -> str:
    """Remove illegal filesystem characters"""
    return re.sub(r'[\\/*?:"<>| ]+', '_', name.strip())[:80]  # limit length


def get_bombay_data(save_folder: str, from_date: str = "", to_date: str = "",
                    coram: Optional[str] = None, actcode: Optional[str] = None,
                    m_bench: Optional[str] = None, m_skey: Optional[str] = None,
                    joflag: Optional[str] = None, m_no: Optional[str] = None,
                    party: Optional[str] = None, ncitation1: Optional[str] = None,
                    ncitation2: Optional[str] = None, ncitation4: Optional[str] = None):

    DATA_URL = "https://bombayhighcourt.nic.in/ord_qry.php"
    data = {
        "CSRFName": "",
        "CSRFToken": "",
        "pageno": "1",
        "actside": "",
        "m_frmdate": from_date or "",
        "m_todate": to_date or "",
        "captcha_code": "1",
        "submit1": "Submit"
    }

    if coram: data["coram"] = coram
    if actcode: data["actcode"] = actcode
    if m_bench: data["m_bench"] = m_bench
    if m_skey: data["m_skey"] = m_skey
    if joflag: data["joflag"] = joflag
    if m_no: data["m_no"] = m_no
    if party: data["party"] = party
    if ncitation1: data["ncitation1"] = ncitation1
    if ncitation2: data["ncitation2"] = ncitation2
    if ncitation4: data["ncitation4"] = ncitation4

    response = requests.post(DATA_URL, data=data)
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", {"id": "myTable"})
    rows = []

    os.makedirs(save_folder, exist_ok=True)

    if table:
        for row in table.find_all("tr"):
            cols = []
            for cell in row.find_all(["td", "th"]):
                for br in cell.find_all("br"):
                    br.replace_with("\n")

                parts = []

                # (1) Get plain text fragments
                texts = [t.strip() for t in cell.find_all(string=True) if t.strip()]
                if texts:
                    parts.extend(texts)

                # (2) Get all links and download PDFs
                for a_tag in cell.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    link_text = a_tag.get_text(strip=True)

                    filename = sanitize_filename(link_text) + ".pdf"
                    pdf_path = os.path.join(save_folder, filename)
                    full_url = f"https://bombayhighcourt.nic.in/{href.lstrip('/')}"

                    download_pdf(full_url, pdf_path)

                    relative_path = os.path.relpath(pdf_path, os.getcwd())
                    parts.append(f'<a href="/{relative_path}" target="_blank">{link_text}</a>')

                cols.append("<br>".join(parts) if parts else "")

            if cols:
                rows.append(cols)

    return rows


@router.get("/bombay", response_class=HTMLResponse)
async def bombay_form(request: Request):
    return templates.TemplateResponse("bombay_form.html", {"request": request})


@router.post("/bombay/results", response_class=HTMLResponse)
async def bombay_results(
    request: Request,
    from_date: Optional[str] = Form(None),
    to_date: Optional[str] = Form(None),
    coram: Optional[str] = Form(None),
    actcode: Optional[str] = Form(None),
    m_bench: Optional[str] = Form(None),
    m_skey: Optional[str] = Form(None),
    joflag: Optional[str] = Form(None),
    m_no: Optional[str] = Form(None),
    party: Optional[str] = Form(None),
    ncitation1: Optional[str] = Form(None),
    ncitation2: Optional[str] = Form(None),
    ncitation4: Optional[str] = Form(None),
):

    # Convert date formats
    from_date_fmt = convert_date_format(from_date) if from_date else ""
    to_date_fmt = convert_date_format(to_date) if to_date else ""

    # === Create folder structure ===
    today_folder = datetime.now().strftime("%Y-%m-%d")

    # Build subfolder name from search params
    folder_name_parts = []
    for key, val in {
        "coram": coram, "actcode": actcode, "m_bench": m_bench,
        "m_skey": m_skey, "joflag": joflag, "m_no": m_no,
        "party": party, "ncitation1": ncitation1, "ncitation2": ncitation2, "ncitation4": ncitation4
    }.items():
        if val:
            folder_name_parts.append(f"{key}={sanitize_filename(val)}")

    if from_date_fmt or to_date_fmt:
        folder_name_parts.append(f"from={from_date_fmt}_to={to_date_fmt}")

    subfolder_name = "__".join(folder_name_parts) or "search_results"
    save_folder = os.path.join("PDFs", today_folder, subfolder_name)
    os.makedirs(save_folder, exist_ok=True)

    # === Fetch data and download PDFs ===
    rows = get_bombay_data(
        save_folder, from_date_fmt, to_date_fmt,
        coram, actcode, m_bench, m_skey, joflag,
        m_no, party, ncitation1, ncitation2, ncitation4
    )

    # === Render and save HTML result page ===
    rendered_html = templates.get_template("bombay_results.html").render(
        request=request, rows=rows, from_date=from_date_fmt, to_date=to_date_fmt
    )
    result_html_path = os.path.join(save_folder, "result.html")
    with open(result_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    return HTMLResponse(rendered_html)

@router.get("/autocomplete/caseType", response_class=JSONResponse)
async def autocomplete_justice(q: str = Query(...)):
    url = f"https://bombayhighcourt.nic.in/ajax/get_skey.php?m_bench=&m_sideflg=&q={q}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return [s.strip() for s in response.text.strip().split("\n") if s.strip()]

@router.get("/autocomplete/act", response_class=JSONResponse)
async def autocomplete_act(q: str = Query(...)):
    url = f"https://bombayhighcourt.nic.in/ajax/get_act.php?m_bench=C&q={q}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return [s.strip() for s in response.text.strip().split("\n") if s.strip()]
