from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from courts import (
    bombay,
    kerala,
    andhra,
    assam,
    telangana,
    hc,
    delhi,
    punjab,
    chhattisgarh,
    sc
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# 📌 Court list for dashboard
court_list = [
    {"name": "Bombay High Court", "description": "Judgements", "url": "/bombay"},
    {"name": "Kerala High Court", "description": "Judgements", "url": "/kerala"},
    {"name": "Andhra Pradesh High Court", "description": "Judgements", "url": "/andhra"},
    {"name": "Assam High Court", "description": "Judgements", "url": "/assam"},
    # {"name": "Telangana High Court", "description": "Judgements", "url": "/telangana"},
    # {"name": "Delhi High Court", "description": "Judgements", "url": "/delhi"},
    # {"name": "Punjab & Haryana High Court", "description": "Judgements", "url": "/punjab"},
    # {"name": "Chhattisgarh High Court", "description": "Judgements", "url": "/chhattisgarh"},
    # {"name": "Supreme Court of India", "description": "Judgements", "url": "/sc"},
]

@app.get("/", response_class=HTMLResponse)
async def court_dashboard(request: Request):
    return templates.TemplateResponse(
        "court_list.html",
        {"request": request, "courts": court_list}
    )

app.include_router(bombay.router)
app.include_router(kerala.router)
app.include_router(andhra.router)
app.include_router(assam.router)
app.include_router(telangana.router)
app.include_router(hc.router)
app.include_router(delhi.router)
app.include_router(punjab.router)
app.include_router(chhattisgarh.router)
app.include_router(sc.router)
