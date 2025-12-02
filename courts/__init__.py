from fastapi import APIRouter
from . import andhra, bombay, chhattisgarh, kerala, assam,calcutta, sc ,telangana, hc, delhi, punjab

router = APIRouter()

# Include each court's router
router.include_router(andhra.router, tags=["Andhra Pradesh High Court"])
router.include_router(bombay.router, tags=["Bombay High Court"])
router.include_router(kerala.router, tags=["Kerala High Court"])
router.include_router(assam.router, tags=["Assam High Court"])
router.include_router(calcutta.router, tags=["Calcutta High Court"])    
router.include_router(telangana.router, tags=["Telangana High Court"])
router.include_router(hc.router, tags=["High Court"])
router.include_router(delhi.router, tags=["Delhi High Court"])
router.include_router(punjab.router, tags=["Punjab High Court"])
router.include_router(chhattisgarh.router, tags=["Chhattisgarh High Court"])
router.include_router(sc.router, tags=["Supreme Court of India"])