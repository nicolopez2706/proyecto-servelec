"""Arma una carpeta de planillas de prueba para probar el portal sin tocar el servidor real.
Uso:  python crear_docs_prueba.py    (crea DOCS_ROOT del .env, por defecto backend/docs_prueba)
Crea .xlsx y, si LibreOffice esta instalado, tambien algunos .ods y .xls para probar esos formatos."""
import subprocess
from pathlib import Path
from openpyxl import Workbook
from app import engine
from app.config import settings

# (carpeta, archivo, extension final)
FILES = [("MODULOS/MDA100", "MDA100ASFAA_L1V1R0_14-07-22", ".xlsx"), ("MODULOS/MDA200", "MDA200APBA_L1V1R0_01-02-25", ".ods"),
         ("MODULOS/MDA300", "MDA300ASFBA_L1V1R1_10-10-24", ".xlsx"), ("DISIPADORES/DIS400", "DIS400AA_L1V1R0_05-05-25", ".xls"),
         ("DISIPADORES/DIS500", "DIS500AA_D1V1R0_05-05-25", ".xlsx"), ("DISIPADORES/DIS600", "DIS600AA_L1V1R0_05-05-25", ".ods")]
ROWS = [["DIS206AA", "Disipador 206", 4], ["CABU6.00", "Cable 6 mm", 10], ["TOR-M6", "Tornillo M6", 24], ["ARA8-ZIN", "Arandela 8 mm zincada", 12]]


def main():
    root = Path(settings.docs_root)
    exe = engine.find_soffice()
    for rel, name, ext in FILES:
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        wb = Workbook(); ws = wb.active; ws.title = "Items"
        ws.append(["Codigo", "Descripcion", "Cantidad"])
        for r in ROWS:
            ws.append(r)
        x = d / f"{name}.xlsx"
        wb.save(x)
        if ext != ".xlsx":
            if not exe:
                print(f"  (sin LibreOffice: {name} queda como .xlsx)")
                continue
            subprocess.run([exe, "--headless", "--convert-to", ext[1:], "--outdir", str(d), str(x)], capture_output=True, timeout=180)
            x.unlink()
    print("Planillas de prueba creadas en", root)


if __name__ == "__main__":
    main()
