from openpyxl import Workbook

wb = Workbook()
ws = wb.active
ws.title = "Items"
ws.append(["Codigo", "Descripcion", "Cantidad"])
ws.append(["A-100", "Cable 2.5mm", 10])
ws.append(["A-200", "Tablero principal", 2])
ws.append(["A-100", "Cable 2.5mm (deposito)", 5])
wb.save("prueba.xlsx")
print("Creado prueba.xlsx")