import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Create a single session to preserve cookies
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# =====================================================
# Utility: Extract Judgement Table
# =====================================================
def extract_results_table(html: str):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "tables11"})
    if not table:
        return "<p>No results table found.</p>"

    rows = [row for row in table.find_all("tr") if len(row.find_all("td")) >= 5]
    seen_cases = set()
    results = []

    for row in rows:
        cols = row.find_all("td")
        case_link_tag = cols[1].find("a")
        if not case_link_tag:
            continue
        case_number = case_link_tag.text.strip()
        if case_number in seen_cases:
            continue
        seen_cases.add(case_number)

        relative = case_link_tag.get("href", "").strip()
        case_link = "https://phhc.gov.in/" + relative
        party_detail = cols[2].get_text(strip=True)
        judgement_date = cols[3].get_text(strip=True)

        view_order_link = ""
        view_order_tag = cols[4].find("a")
        if view_order_tag and "onclick" in view_order_tag.attrs:
            onclick = view_order_tag["onclick"]
            if "window.open(" in onclick:
                start = onclick.find("('") + 2
                end = onclick.find("')", start)
                partial = onclick[start:end]
                view_order_link = "https://phhc.gov.in/" + partial

        results.append({
            "case_number": case_number,
            "case_link": case_link,
            "party_detail": party_detail,
            "date": judgement_date,
            "view_order_link": view_order_link,
        })

    # --- Render Table with full borders ---
    result_html = """
    <table style="border-collapse: collapse; width:100%;">
        <thead style="background:#343a40; color:#fff;">
            <tr>
                <th style="border:1px solid #000; padding:5px;">Sr. No.</th>
                <th style="border:1px solid #000; padding:5px;">Case No.</th>
                <th style="border:1px solid #000; padding:5px;">Party</th>
                <th style="border:1px solid #000; padding:5px;">Date</th>
                <th style="border:1px solid #000; padding:5px;">View Order</th>
            </tr>
        </thead>
        <tbody>
    """
    for idx, r in enumerate(results, start=1):
        case_detail_url = f"/punjab/case_detail?url={r['case_link']}"
        result_html += f"""
        <tr>
            <td style="border:1px solid #000; padding:5px;">{idx}</td>
            <td style="border:1px solid #000; padding:5px;">
                <a href="{case_detail_url}" target="_blank">{r['case_number']}</a>
            </td>
            <td style="border:1px solid #000; padding:5px;">{r['party_detail']}</td>
            <td style="border:1px solid #000; padding:5px;">{r['date']}</td>
            <td style="border:1px solid #000; padding:5px;">
                <a href="{r['view_order_link']}" target="_blank">View Order</a>
            </td>
        </tr>
        """
    result_html += "</tbody></table>"

    # --- Pagination ---
    pagination_tds = soup.find_all("td", colspan="8")
    if len(pagination_tds) > 1:
        pagination_td = pagination_tds[1]  # second one
        # Convert relative links to absolute URLs
        for a in pagination_td.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                a["href"] = "https://phhc.gov.in/" + href.lstrip("./")
        # Render inside a div below table
        pagination_html = f'<div style="text-align:center; margin-top:10px;">{pagination_td.decode_contents()}</div>'
        result_html += pagination_html

    return result_html


def fix_onclick_links(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", onclick=True):
        onclick = a["onclick"]
        if "window.open(" in onclick:
            start = onclick.find("('") + 2
            end = onclick.find("')", start)
            relative = onclick[start:end]
            pdf_url = "https://phhc.gov.in/" + relative
            a.attrs.pop("onclick")  # remove JS
            a.attrs["href"] = pdf_url
            a.attrs["target"] = "_blank"  # open in new tab
    return str(soup)


# =====================================================
# Route: Form Page
# =====================================================
@router.get("/punjab", response_class=HTMLResponse)
async def show_punjab_form(request: Request):
    """Load Punjab & Haryana HC form dropdowns."""
    url = "https://phhc.gov.in/home.php?search_param=jud_judgement"
    res = session.get(url)
    soup = BeautifulSoup(res.text, "html.parser")

    # --- Judges ---
    judges = []
    judge_select = soup.find("select", {"id": "t_jud_code"})
    if judge_select:
        for opt in judge_select.find_all("option"):
            code = opt.get("value", "").strip()
            name = opt.text.strip()
            if code:
                judges.append({"code": code, "name": name})

    # --- Case Types ---
    case_types = []
    case_select = soup.find("select", {"id": "t_case_type"})
    if case_select:
        for opt in case_select.find_all("option"):
            code = opt.get("value", "").strip()
            name = opt.text.strip()
            if code:
                case_types.append({"code": code, "name": name})

    # --- Case Years ---
    case_years = []
    year_select = soup.find("select", {"id": "t_case_year"})
    if year_select:
        for opt in year_select.find_all("option"):
            code = opt.get("value", "").strip()
            name = opt.text.strip()
            if code:
                case_years.append({"code": code, "name": name})

    return templates.TemplateResponse(
        "punjab_form.html",
        {
            "request": request,
            "judges": judges,
            "case_types": case_types,
            "case_years": case_years
        }
    )


# =====================================================
# Route: Submit Search Form
# =====================================================
@router.post("/punjab/results", response_class=HTMLResponse)
async def fetch_punjab_results(
    request: Request,
    from_date: str = Form(...),
    to_date: str = Form(...),
    t_jud_code: str = Form(""),
    pet_name: str = Form(""),
    free_text: str = Form(""),
    t_case_type: str = Form(""),
    t_case_year: str = Form(""),
    reportable: str = Form("A")
):
    """Fetch search results from PHHC."""
    url = "https://phhc.gov.in/home.php?search_param=jud_judgement"
    payload = {
        "from_date": from_date,
        "to_date": to_date,
        "t_jud_code": t_jud_code,
        "pet_name": pet_name,
        "free_text": free_text,
        "t_case_type": t_case_type,
        "t_case_year": t_case_year,
        "reportable": reportable,
        "submit": "Search Case",
    }

    response = session.post(url, data=payload)
    html = response.text

    # Debug save
    with open("punjab_response.html", "w", encoding="utf-8") as f:
        f.write(html)

    result_html = extract_results_table(html)
    return HTMLResponse(content=result_html)


# =====================================================
# Route: Case Details Proxy
# =====================================================
@router.get("/punjab/case_detail", response_class=HTMLResponse)
async def fetch_case_detail(url: str):
    """
    Fetch case detail page and rewrite JS onclick links to clickable links.
    """
    res = session.get(url)
    fixed_html = fix_onclick_links(res.text)
    return HTMLResponse(content=fixed_html)
