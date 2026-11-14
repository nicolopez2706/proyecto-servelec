import shutil
import sys
from openpyxl import load_workbook

def main():
    if len(sys.argv) != 4:
        print("Uso: python cambiar_codigos.py archivo.xlsx CODIGO_VIEJO CODIGO_NUEVO")
        sys.exit(1)
    archivo, viejo, nuevo = sys.argv[1:4]
    copia = archivo.replace(".xlsx", "_modificado.xlsx")
    shutil.copy(archivo, copia)  # trabaja sobre una copia
    wb = load_workbook(copia)
    cambios = 0
    for ws in wb.worksheets:
        for fila in ws.iter_rows():
            for celda in fila:
                if celda.value == viejo:
                    celda.value = nuevo
                    cambios += 1
    wb.save(copia)
    print(f"Listo: {cambios} cambios en {copia}")

main()