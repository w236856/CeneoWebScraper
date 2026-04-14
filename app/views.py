from app import app
import os
import json
import requests
import pandas as pd
from bs4 import BeautifulSoup
from matplotlib import pyplot as plt
from flask import render_template, request, redirect, url_for, send_file, jsonify
from config import headers
from app import utils
import io
import numpy as np
import matplotlib
matplotlib.use('Agg')

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/extract")
def display_form():
    return render_template("extract.html")

@app.route("/extract", methods=["POST"])
def extract():
    product_id = request.form.get('product_id')
    next_page = f"https://www.ceneo.pl/{product_id}#tab=reviews"
    response = requests.get(next_page, headers=headers)
    if response.status_code == 200:
        page_dom = BeautifulSoup(response.text, "html.parser")
        product_name = utils.extract_feature(page_dom, "h1")
        opinions_count = utils.extract_feature(page_dom, "a.product-review__link > span")
        if not opinions_count:
            error = "Dla produktu o podanym id nie ma jeszcze żadnych opinii"
            return render_template("extract.html", error=error)
    else:
        error = "Nie znaleziono produktu o podanym id"
        return render_template("extract.html", error=error)

    all_opinions = []
    while next_page:
        response = requests.get(next_page, headers=headers)
        if response.status_code == 200:
            page_dom = BeautifulSoup(response.text, "html.parser")
            opinions = page_dom.select("div.js_product-review:not(.user-post--highlight)")
            for opinion in opinions:
                single_opinion = {
                    key: utils.extract_feature(opinion, *value)
                    for key, value in utils.selectors.items()
                }
                all_opinions.append(single_opinion)
            try:
                next_page = "https://www.ceneo.pl" + utils.extract_feature(page_dom, "a.pagination__next", "href")
            except TypeError:
                next_page = None
        else:
            break

    os.makedirs("./app/data/opinions", exist_ok=True)
    os.makedirs("./app/data/products", exist_ok=True)

    with open(f"./app/data/opinions/{product_id}.json", "w", encoding="UTF-8") as jf:
        json.dump(all_opinions, jf, indent=4, ensure_ascii=False)

    opinions = pd.DataFrame.from_dict(all_opinions)
    opinions.stars = opinions.stars.apply(lambda s: s.split("/")[0].replace(",", ".")).astype(float)
    opinions.useful = opinions.useful.astype(int)
    opinions.unuseful = opinions.unuseful.astype(int)

    stats = {
        'product_id': product_id,
        'product_name': product_name,
        "opinions_count": opinions.shape[0],
        "pros_count": int(opinions.pros.astype(bool).sum()),
        "cons_count": int(opinions.cons.astype(bool).sum()),
        "pros_cons_count": int(opinions.apply(lambda o: bool(o.pros) and bool(o.cons), axis=1).sum()),
        "average_stars": float(opinions.stars.mean()),
        "pros": opinions.pros.explode().dropna().value_counts().to_dict(),
        "cons": opinions.cons.explode().dropna().value_counts().to_dict(),
        "recommendations": opinions.recommendation.value_counts(dropna=False).reindex(['Nie polecam', 'Polecam', None], fill_value=0).to_dict(),
    }

    with open(f"./app/data/products/{product_id}.json", "w", encoding="UTF-8") as jf:
        json.dump(stats, jf, indent=4, ensure_ascii=False)

    return redirect(url_for('product', product_id=product_id, product_name=product_name))

@app.route("/products")
def products():
    products_files = os.listdir("./app/data/products")
    products_list = []
    for filename in products_files:
        with open(f"./app/data/products/{filename}", "r", encoding="UTF-8") as jf:
            product = json.load(jf)
            products_list.append(product)
    return render_template("products.html", products=products_list)

@app.route("/author")
def author():
    return render_template("author.html")

@app.route("/product/<product_id>")
def product(product_id):
    product_name = request.args.get('product_name')
    with open(f"./app/data/opinions/{product_id}.json", "r", encoding="UTF-8") as jf:
        opinions = json.load(jf)
    return render_template("product.html", product_id=product_id, product_name=product_name, opinions=opinions)

@app.route("/charts/<product_id>")
def charts(product_id):
    os.makedirs("./app/static/images/charts", exist_ok=True)

    with open(f"./app/data/products/{product_id}.json", "r", encoding="UTF-8") as jf:
        stats = json.load(jf)

    recommendations = pd.Series(stats['recommendations'])
    recommendations.plot.pie(
        label="",
        title=f"Rozkład rekomendacji w opiniach o produkcie {product_id}",
        labels=['Nie polecam', 'Polecam', "Nie mam zdania"],
        colors=["crimson", 'forestgreen', "lightgrey"],
        autopct="%1.1f%%"
    )
    plt.savefig(f"./app/static/images/charts/{stats['product_id']}_pie.png")
    plt.close()

    with open(f"./app/data/opinions/{product_id}.json", "r", encoding="UTF-8") as jf:
        opinions = pd.DataFrame(json.load(jf))
    opinions.stars = opinions.stars.apply(lambda s: float(s.split("/")[0].replace(",", ".")))

    stars_counts = opinions.stars.value_counts().sort_index()
    plt.figure(figsize=(8,5))
    stars_counts.plot.bar(
        color="skyblue",
        title=f"Liczba opinii z poszczególnymi ocenami gwiazdkowymi dla produktu {product_id}"
    )
    plt.xlabel("Liczba gwiazdek")
    plt.ylabel("Liczba opinii")
    plt.xticks(rotation=0)
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    plt.tight_layout()
    plt.savefig(f"./app/static/images/charts/{stats['product_id']}_bar.png")
    plt.close()

    return render_template("charts.html", product_id=product_id, product_name=stats['product_name'])

@app.route('/export/<product_id>/<format>')
def export_product(product_id, format):
    data = get_product_data(product_id)
    if not data:
        return "Brak danych dla tego produktu", 404

    df = pd.DataFrame(data)

    if format == 'csv':
        buffer = io.StringIO()
        df.to_csv(buffer, index=False)
        buffer.seek(0)
        return send_file(
            io.BytesIO(buffer.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'product_{product_id}.csv'
        )

    elif format == 'xlsx':
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Opinie')
            writer.close()  
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'product_{product_id}.xlsx'
        )

    elif format == 'json':
        return jsonify(data)

    else:
        return "Unsupported format", 400

# Funkcja pomocnicza do pobierania danych
def get_product_data(product_id):
    file_path = f"./app/data/opinions/{product_id}.json"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="UTF-8") as jf:
            return json.load(jf)
    else:
        return []