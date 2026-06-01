from flask import Flask, render_template, request, redirect, url_for, flash, session
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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_servicio TEXT NOT NULL,
            proveedor TEXT NOT NULL,
            valor_total REAL NOT NULL,
            meses INTEGER NOT NULL,
            dia_pago INTEGER NOT NULL,
            fecha_registro TEXT NOT NULL,
            valor_mensual REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuotas_servicios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            servicio_id INTEGER NOT NULL,
            numero_cuota INTEGER NOT NULL,
            fecha_pago TEXT NOT NULL,
            valor_base REAL NOT NULL,
            incremento_mora REAL DEFAULT 0,
            valor_total REAL NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_pagada TEXT,
            FOREIGN KEY(servicio_id) REFERENCES servicios(id)
        )
    """)

    conn.commit()
    conn.close()


def login_required():
    return session.get("usuario") == "admin"


def fecha(fecha_str):
    return datetime.strptime(fecha_str, "%Y-%m-%d").date()


def estado_item(item):
    hoy = date.today()
    fecha_pago = fecha(item["fecha_pago"])

    if item["estado"] == "pagado":
        return "Pagado", "green", 0

    dias = (fecha_pago - hoy).days

    if dias < 0:
        return "Vencido", "red", abs(dias)

    if dias <= 2:
        return "Próximo a vencer", "yellow", 0

    return "Pendiente", "blue", 0


def crear_cuotas(tabla, id_columna, id_valor, meses, fecha_registro, dia_pago, valor_mensual):
    conn = get_connection()
    fecha_base = fecha(fecha_registro)

    for i in range(1, meses + 1):
        fecha_cuota = fecha_base + relativedelta(months=i)
        fecha_cuota = fecha_cuota.replace(day=min(dia_pago, 28))

        conn.execute(f"""
            INSERT INTO {tabla}
            ({id_columna}, numero_cuota, fecha_pago, valor_base, incremento_mora, valor_total, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            id_valor,
            i,
            fecha_cuota.strftime("%Y-%m-%d"),
            valor_mensual,
            0,
            valor_mensual,
            "pendiente"
        ))

    conn.commit()
    conn.close()


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"]
        password = request.form["password"]

        if usuario == "admin" and password == "123456789":
            session["usuario"] = "admin"
            return redirect(url_for("menu"))

        flash("Usuario o contraseña incorrectos.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/menu")
def menu():
    if not login_required():
        return redirect(url_for("login"))
    return render_template("menu.html")


@app.route("/prestamos")
def prestamos():
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()
    prestamos_raw = conn.execute("SELECT * FROM prestamos ORDER BY id DESC").fetchall()

    lista = []

    for p in prestamos_raw:
        cuotas = conn.execute("SELECT * FROM cuotas WHERE prestamo_id = ? ORDER BY numero_cuota", (p["id"],)).fetchall()
        pagadas = sum(1 for c in cuotas if c["estado"] == "pagado")
        vencidas = 0
        deuda_actual = 0

        for c in cuotas:
            estado_texto, color, dias = estado_item(c)
            if c["estado"] != "pagado":
                deuda_actual += c["valor_total"]
            if estado_texto == "Vencido":
                vencidas += 1

        pendiente = next((c for c in cuotas if c["estado"] != "pagado"), None)

        if pendiente:
            semaforo, color, _ = estado_item(pendiente)
            proxima_fecha = pendiente["fecha_pago"]
        else:
            semaforo, color, proxima_fecha = "Pagado", "green", "Finalizado"

        item = dict(p)
        item["cuotas_pagadas"] = pagadas
        item["cuotas_vencidas"] = vencidas
        item["deuda_actual"] = deuda_actual
        item["proxima_fecha"] = proxima_fecha
        item["semaforo"] = semaforo
        item["color"] = color
        lista.append(item)

    conn.close()

    return render_template(
        "prestamos.html",
        prestamos=lista,
        total_prestado=sum(p["valor_prestado"] for p in lista),
        total_mensual=sum(p["valor_mensual_base"] for p in lista),
        total_con_interes=sum(p["total_con_interes"] for p in lista),
        total_personas=len(lista)
    )


@app.route("/crear_prestamo", methods=["POST"])
def crear_prestamo():
    if not login_required():
        return redirect(url_for("login"))

    nombre = request.form["nombre"]
    valor_prestado = float(request.form["valor_prestado"])
    meses = int(request.form["meses"])
    interes = float(request.form["interes"])
    dia_pago = int(request.form["dia_pago"])
    fecha_registro = request.form["fecha_registro"]

    total_con_interes = valor_prestado + (valor_prestado * interes / 100)
    valor_mensual = total_con_interes / meses

    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO prestamos
        (nombre, valor_prestado, meses, interes, dia_pago, fecha_registro, valor_mensual_base, total_con_interes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (nombre, valor_prestado, meses, interes, dia_pago, fecha_registro, valor_mensual, total_con_interes))

    prestamo_id = cursor.lastrowid
    conn.commit()
    conn.close()

    crear_cuotas("cuotas", "prestamo_id", prestamo_id, meses, fecha_registro, dia_pago, valor_mensual)

    flash("Préstamo creado correctamente.")
    return redirect(url_for("prestamos"))


@app.route("/detalle_prestamo/<int:id>")
def detalle_prestamo(id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()
    prestamo = conn.execute("SELECT * FROM prestamos WHERE id = ?", (id,)).fetchone()
    cuotas_raw = conn.execute("SELECT * FROM cuotas WHERE prestamo_id = ? ORDER BY numero_cuota", (id,)).fetchall()
    conn.close()

    cuotas = []
    for c in cuotas_raw:
        estado_texto, color, dias = estado_item(c)
        item = dict(c)
        item["estado_texto"] = estado_texto
        item["color"] = color
        item["dias_vencidos"] = dias
        cuotas.append(item)

    return render_template("detalle_prestamo.html", prestamo=prestamo, cuotas=cuotas)


@app.route("/servicios")
def servicios():
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()
    servicios_raw = conn.execute("SELECT * FROM servicios ORDER BY id DESC").fetchall()

    lista = []

    for s in servicios_raw:
        cuotas = conn.execute("SELECT * FROM cuotas_servicios WHERE servicio_id = ? ORDER BY numero_cuota", (s["id"],)).fetchall()
        pagadas = sum(1 for c in cuotas if c["estado"] == "pagado")
        vencidas = 0
        deuda_actual = 0

        for c in cuotas:
            estado_texto, color, dias = estado_item(c)
            if c["estado"] != "pagado":
                deuda_actual += c["valor_total"]
            if estado_texto == "Vencido":
                vencidas += 1

        pendiente = next((c for c in cuotas if c["estado"] != "pagado"), None)

        if pendiente:
            semaforo, color, _ = estado_item(pendiente)
            proxima_fecha = pendiente["fecha_pago"]
        else:
            semaforo, color, proxima_fecha = "Pagado", "green", "Finalizado"

        item = dict(s)
        item["cuotas_pagadas"] = pagadas
        item["cuotas_vencidas"] = vencidas
        item["deuda_actual"] = deuda_actual
        item["proxima_fecha"] = proxima_fecha
        item["semaforo"] = semaforo
        item["color"] = color
        lista.append(item)

    conn.close()

    return render_template(
        "servicios.html",
        servicios=lista,
        total_servicios=sum(s["valor_total"] for s in lista),
        total_mensual=sum(s["valor_mensual"] for s in lista),
        total_pendiente=sum(s["deuda_actual"] for s in lista),
        cantidad_servicios=len(lista)
    )


@app.route("/crear_servicio", methods=["POST"])
def crear_servicio():
    if not login_required():
        return redirect(url_for("login"))

    nombre_servicio = request.form["nombre_servicio"]
    proveedor = request.form["proveedor"]
    valor_total = float(request.form["valor_total"])
    meses = int(request.form["meses"])
    dia_pago = int(request.form["dia_pago"])
    fecha_registro = request.form["fecha_registro"]

    valor_mensual = valor_total / meses

    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO servicios
        (nombre_servicio, proveedor, valor_total, meses, dia_pago, fecha_registro, valor_mensual)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (nombre_servicio, proveedor, valor_total, meses, dia_pago, fecha_registro, valor_mensual))

    servicio_id = cursor.lastrowid
    conn.commit()
    conn.close()

    crear_cuotas("cuotas_servicios", "servicio_id", servicio_id, meses, fecha_registro, dia_pago, valor_mensual)

    flash("Servicio creado correctamente.")
    return redirect(url_for("servicios"))


@app.route("/detalle_servicio/<int:id>")
def detalle_servicio(id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()
    servicio = conn.execute("SELECT * FROM servicios WHERE id = ?", (id,)).fetchone()
    cuotas_raw = conn.execute("SELECT * FROM cuotas_servicios WHERE servicio_id = ? ORDER BY numero_cuota", (id,)).fetchall()
    conn.close()

    cuotas = []
    for c in cuotas_raw:
        estado_texto, color, dias = estado_item(c)
        item = dict(c)
        item["estado_texto"] = estado_texto
        item["color"] = color
        item["dias_vencidos"] = dias
        cuotas.append(item)

    return render_template("detalle_servicio.html", servicio=servicio, cuotas=cuotas)


@app.route("/pagar/<tipo>/<int:id>")
def pagar(tipo, id):
    if not login_required():
        return redirect(url_for("login"))

    tabla = "cuotas" if tipo == "prestamo" else "cuotas_servicios"

    conn = get_connection()
    conn.execute(f"""
        UPDATE {tabla}
        SET estado = 'pagado', fecha_pagada = ?
        WHERE id = ?
    """, (date.today().strftime("%Y-%m-%d"), id))
    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("menu"))


@app.route("/editar_cuota/<tipo>/<int:id>", methods=["POST"])
def editar_cuota(tipo, id):
    if not login_required():
        return redirect(url_for("login"))

    tabla = "cuotas" if tipo == "prestamo" else "cuotas_servicios"

    fecha_pago = request.form["fecha_pago"]
    incremento_mora = float(request.form["incremento_mora"])
    estado = request.form["estado"]

    conn = get_connection()
    cuota = conn.execute(f"SELECT * FROM {tabla} WHERE id = ?", (id,)).fetchone()
    valor_total = cuota["valor_base"] + incremento_mora

    conn.execute(f"""
        UPDATE {tabla}
        SET fecha_pago = ?, incremento_mora = ?, valor_total = ?, estado = ?
        WHERE id = ?
    """, (fecha_pago, incremento_mora, valor_total, estado, id))

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("menu"))


@app.route("/borrar/<tipo>/<int:id>", methods=["POST"])
def borrar(tipo, id):
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()

    if tipo == "prestamo":
        conn.execute("DELETE FROM cuotas WHERE prestamo_id = ?", (id,))
        conn.execute("DELETE FROM prestamos WHERE id = ?", (id,))
        destino = "prestamos"
    else:
        conn.execute("DELETE FROM cuotas_servicios WHERE servicio_id = ?", (id,))
        conn.execute("DELETE FROM servicios WHERE id = ?", (id,))
        destino = "servicios"

    conn.commit()
    conn.close()

    flash("Registro eliminado correctamente.")
    return redirect(url_for(destino))


@app.route("/dashboard")
def dashboard():
    if not login_required():
        return redirect(url_for("login"))

    conn = get_connection()

    prestamos = conn.execute("SELECT * FROM prestamos").fetchall()
    servicios = conn.execute("SELECT * FROM servicios").fetchall()
    cuotas = conn.execute("SELECT * FROM cuotas").fetchall()
    cuotas_servicios = conn.execute("SELECT * FROM cuotas_servicios").fetchall()

    conn.close()

    total_prestamos = sum(p["valor_prestado"] for p in prestamos)
    total_servicios = sum(s["valor_total"] for s in servicios)

    cuotas_vencidas = 0
    cuotas_proximas = 0
    cuotas_pendientes = 0
    cuotas_pagadas = 0

    for grupo in [cuotas, cuotas_servicios]:
        for c in grupo:
            estado_texto, _, _ = estado_item(c)

            if c["estado"] == "pagado":
                cuotas_pagadas += 1
            elif estado_texto == "Vencido":
                cuotas_vencidas += 1
            elif estado_texto == "Próximo a vencer":
                cuotas_proximas += 1
            else:
                cuotas_pendientes += 1

    return render_template(
        "dashboard.html",
        total_prestamos=total_prestamos,
        total_servicios=total_servicios,
        cuotas_vencidas=cuotas_vencidas,
        cuotas_proximas=cuotas_proximas,
        cuotas_pendientes=cuotas_pendientes,
        cuotas_pagadas=cuotas_pagadas,
        total_general=total_prestamos + total_servicios
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)