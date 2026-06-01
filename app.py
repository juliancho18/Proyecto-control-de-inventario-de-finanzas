from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
import os

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
            fecha_pago TEXT NOT NULL,
            valor_mensual REAL NOT NULL,
            total_con_interes REAL NOT NULL,
            estado TEXT DEFAULT 'pendiente'
        )
    """)
    conn.commit()
    conn.close()


@app.route("/")
def home():
    conn = get_connection()
    prestamos = conn.execute("SELECT * FROM prestamos ORDER BY id DESC").fetchall()
    conn.close()

    total_prestado = sum(p["valor_prestado"] for p in prestamos)
    total_mensual = sum(p["valor_mensual"] for p in prestamos)
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
    fecha_pago = request.form["fecha_pago"]

    total_con_interes = valor_prestado + (valor_prestado * interes / 100)
    valor_mensual = total_con_interes / meses

    conn = get_connection()
    conn.execute("""
        INSERT INTO prestamos
        (nombre, valor_prestado, meses, interes, fecha_pago, valor_mensual, total_con_interes, estado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        nombre,
        valor_prestado,
        meses,
        interes,
        fecha_pago,
        valor_mensual,
        total_con_interes,
        "pendiente"
    ))
    conn.commit()
    conn.close()

    flash("Registro guardado correctamente.")
    return redirect(url_for("home"))


@app.route("/estado/<int:id>/<estado>")
def cambiar_estado(id, estado):
    conn = get_connection()
    conn.execute("UPDATE prestamos SET estado = ? WHERE id = ?", (estado, id))
    conn.commit()
    conn.close()
    return redirect(url_for("home"))


@app.route("/borrar/<int:id>", methods=["POST"])
def borrar(id):
    conn = get_connection()
    conn.execute("DELETE FROM prestamos WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    flash("Registro eliminado correctamente.")
    return redirect(url_for("home"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)