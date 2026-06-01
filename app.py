from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import os
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")
app.secret_key = "clave_desarrollo"

DB_PATH = "instance/finanzas.db"


def get_connection():
    os.makedirs("instance", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prestamos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            valor_prestado REAL NOT NULL,
            meses INTEGER NOT NULL,
            interes REAL NOT NULL,
            dia_pago INTEGER NOT NULL,
            fecha_registro TEXT NOT NULL,
            valor_mensual_base REAL NOT NULL,
            total_con_interes REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prestamo_id INTEGER NOT NULL,
            numero_cuota INTEGER NOT NULL,
            fecha_pago TEXT NOT NULL,
            valor_base REAL NOT NULL,
            incremento_mora REAL DEFAULT 0,
            valor_total REAL NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_pagada TEXT,
            FOREIGN KEY(prestamo_id) REFERENCES prestamos(id)
        )
    """)

    conn.commit()
    conn.close()


def formato_fecha(fecha_str):
    return datetime.strptime(fecha_str, "%Y-%m-%d").date()


def estado_cuota(cuota):
    hoy = date.today()
    fecha_pago = formato_fecha(cuota["fecha_pago"])

    if cuota["estado"] == "pagado":
        return "Pagado", "green", 0

    dias = (fecha_pago - hoy).days

    if dias < 0:
        return "Vencido", "red", abs(dias)

    if dias <= 2:
        return "Próximo a vencer", "yellow", 0

    return "Pendiente", "blue", 0


def preparar_resumen():
    conn = get_connection()

    prestamos = conn.execute("SELECT * FROM prestamos ORDER BY id DESC").fetchall()

    resumen = []

    for p in prestamos:
        cuotas = conn.execute(
            "SELECT * FROM cuotas WHERE prestamo_id = ? ORDER BY numero_cuota ASC",
            (p["id"],)
        ).fetchall()

        cuotas_pagadas = sum(1 for c in cuotas if c["estado"] == "pagado")
        cuotas_vencidas = 0
        deuda_actual = 0
        proxima_fecha = "Sin cuotas"
        semaforo = "Pagado"
        color = "green"

        for c in cuotas:
            estado_texto, estado_color, dias_vencidos = estado_cuota(c)

            if c["estado"] != "pagado":
                deuda_actual += c["valor_total"]

            if estado_texto == "Vencido":
                cuotas_vencidas += 1

        pendiente = next((c for c in cuotas if c["estado"] != "pagado"), None)

        if pendiente:
            estado_texto, estado_color, dias_vencidos = estado_cuota(pendiente)
            proxima_fecha = pendiente["fecha_pago"]
            semaforo = estado_texto
            color = estado_color

        resumen.append({
            "id": p["id"],
            "nombre": p["nombre"],
            "valor_prestado": p["valor_prestado"],
            "meses": p["meses"],
            "interes": p["interes"],
            "dia_pago": p["dia_pago"],
            "fecha_registro": p["fecha_registro"],
            "valor_mensual_base": p["valor_mensual_base"],
            "total_con_interes": p["total_con_interes"],
            "cuotas_pagadas": cuotas_pagadas,
            "cuotas_vencidas": cuotas_vencidas,
            "deuda_actual": deuda_actual,
            "proxima_fecha": proxima_fecha,
            "semaforo": semaforo,
            "color": color
        })

    conn.close()
    return resumen


@app.route("/")
def home():
    prestamos = preparar_resumen()

    total_prestado = sum(p["valor_prestado"] for p in prestamos)
    total_mensual = sum(p["valor_mensual_base"] for p in prestamos)
    total_con_interes = sum(p["total_con_interes"] for p in prestamos)

    return render_template(
        "index.html",
        prestamos=prestamos,
        total_prestado=total_prestado,
        total_mensual=total_mensual,
        total_con_interes=total_con_interes,
        total_personas=len(prestamos)
    )


@app.route("/crear", methods=["POST"])
def crear():
    nombre = request.form["nombre"]
    valor_prestado = float(request.form["valor_prestado"])
    meses = int(request.form["meses"])
    interes = float(request.form["interes"])
    dia_pago = int(request.form["dia_pago"])
    fecha_registro = request.form["fecha_registro"]

    total_con_interes = valor_prestado + (valor_prestado * interes / 100)
    valor_mensual_base = total_con_interes / meses

    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO prestamos
        (nombre, valor_prestado, meses, interes, dia_pago, fecha_registro, valor_mensual_base, total_con_interes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        valor_prestado,
        meses,
        interes,
        dia_pago,
        fecha_registro,
        valor_mensual_base,
        total_con_interes
    ))

    prestamo_id = cursor.lastrowid

    fecha_base = formato_fecha(fecha_registro)

    for i in range(1, meses + 1):
        fecha_cuota = fecha_base + relativedelta(months=i)
        fecha_cuota = fecha_cuota.replace(day=min(dia_pago, 28))

        conn.execute("""
            INSERT INTO cuotas
            (prestamo_id, numero_cuota, fecha_pago, valor_base, incremento_mora, valor_total, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            prestamo_id,
            i,
            fecha_cuota.strftime("%Y-%m-%d"),
            valor_mensual_base,
            0,
            valor_mensual_base,
            "pendiente"
        ))

    conn.commit()
    conn.close()

    flash("Préstamo y cuotas creados correctamente.")
    return redirect(url_for("home"))


@app.route("/detalle/<int:id>")
def detalle(id):
    conn = get_connection()

    prestamo = conn.execute("SELECT * FROM prestamos WHERE id = ?", (id,)).fetchone()
    cuotas_raw = conn.execute(
        "SELECT * FROM cuotas WHERE prestamo_id = ? ORDER BY numero_cuota ASC",
        (id,)
    ).fetchall()

    conn.close()

    cuotas = []
    for c in cuotas_raw:
        estado_texto, color, dias_vencidos = estado_cuota(c)
        item = dict(c)
        item["estado_texto"] = estado_texto
        item["color"] = color
        item["dias_vencidos"] = dias_vencidos
        cuotas.append(item)

    return render_template("detalle.html", prestamo=prestamo, cuotas=cuotas)


@app.route("/pagar_cuota/<int:id>")
def pagar_cuota(id):
    hoy = date.today().strftime("%Y-%m-%d")

    conn = get_connection()
    conn.execute("""
        UPDATE cuotas
        SET estado = 'pagado', fecha_pagada = ?
        WHERE id = ?
    """, (hoy, id))
    conn.commit()
    conn.close()

    flash("Cuota marcada como pagada.")
    return redirect(request.referrer or url_for("home"))


@app.route("/editar_cuota/<int:id>", methods=["POST"])
def editar_cuota(id):
    fecha_pago = request.form["fecha_pago"]
    incremento_mora = float(request.form["incremento_mora"])
    estado = request.form["estado"]

    conn = get_connection()
    cuota = conn.execute("SELECT * FROM cuotas WHERE id = ?", (id,)).fetchone()

    valor_total = cuota["valor_base"] + incremento_mora

    conn.execute("""
        UPDATE cuotas
        SET fecha_pago = ?, incremento_mora = ?, valor_total = ?, estado = ?
        WHERE id = ?
    """, (
        fecha_pago,
        incremento_mora,
        valor_total,
        estado,
        id
    ))

    conn.commit()
    conn.close()

    flash("Cuota actualizada correctamente.")
    return redirect(request.referrer or url_for("home"))


@app.route("/borrar/<int:id>", methods=["POST"])
def borrar(id):
    conn = get_connection()
    conn.execute("DELETE FROM cuotas WHERE prestamo_id = ?", (id,))
    conn.execute("DELETE FROM prestamos WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    flash("Préstamo eliminado correctamente.")
    return redirect(url_for("home"))


@app.route("/admin")
def admin():
    conn = get_connection()

    prestamos = conn.execute("SELECT * FROM prestamos ORDER BY id DESC").fetchall()
    cuotas = conn.execute("""
        SELECT cuotas.*, prestamos.nombre 
        FROM cuotas
        INNER JOIN prestamos ON prestamos.id = cuotas.prestamo_id
        ORDER BY prestamos.id DESC, cuotas.numero_cuota ASC
    """).fetchall()

    conn.close()
    return render_template("admin.html", prestamos=prestamos, cuotas=cuotas)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)