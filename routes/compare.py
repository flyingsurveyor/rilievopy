"""
Compare two survey points routes.
"""

from flask import Blueprint, render_template, request

from modules.compare import compare_points
from modules.survey import list_all_points_options, load_survey, point_from_feature

bp = Blueprint('compare', __name__)


@bp.route("/compare", methods=["GET","POST"])
def compare():
    opts = list_all_points_options()
    options = "\n".join([f'<option value="{v}">{l}</option>' for (l,v) in opts]) or '<option disabled>(nessun punto)</option>'
    result_html = ""
    if request.method == "POST":
        a = request.form.get("a",""); b = request.form.get("b","")
        if a == b:
            result_html = '<div class="card"><b>Seleziona due punti diversi.</b></div>'
        else:
            try:
                sidA,pidA = a.split("|",1); sidB,pidB = b.split("|",1)
                svyA = load_survey(sidA); svyB = load_survey(sidB)
                FA = next((f for f in svyA.get("features", []) if f.get("id")==pidA), None)
                FB = next((f for f in svyB.get("features", []) if f.get("id")==pidB), None)
                if not (FA and FB): raise ValueError("Punto non trovato")
                A = point_from_feature(FA); B = point_from_feature(FB)
                res = compare_points(A,B)
                if not res.get("ok"):
                    result_html = f'<div class="card"><b>Errore:</b> {res.get("err","-")}</div>'
                else:
                    def fmt(x,d=4): return ("-" if x is None else f"{x:.{d}f}")
                    lines=[]
                    lines.append('<div class="card"><h1>Risultati confronto</h1>')
                    lines.append('<div class="kv"><span class="k">Punto A</span><span class="v">%s</span></div>' % (A.get("name") or f"{sidA}/{pidA}"))
                    lines.append('<div class="kv"><span class="k">Punto B</span><span class="v">%s</span></div>' % (B.get("name") or f"{sidB}/{pidB}"))
                    lines.append('<div class="kv"><span class="k">ΔE</span><span class="v">%s m</span></div>' % fmt(res["ΔE"],4))
                    lines.append('<div class="kv"><span class="k">ΔN</span><span class="v">%s m</span></div>' % fmt(res["ΔN"],4))
                    lines.append('<div class="kv"><span class="k">ΔU</span><span class="v">%s m</span></div>' % fmt(res["ΔU"],4))
                    lines.append('<div class="kv"><span class="k">Distanza orizzontale</span><span class="v">%s m</span></div>' % fmt(res["horiz"],4))
                    lines.append('<div class="kv"><span class="k">Distanza 3D</span><span class="v">%s m</span></div>' % fmt(res["dist3D"],4))
                    lines.append('<div class="kv"><span class="k">Bearing (da Nord)</span><span class="v">%s °</span></div>' % fmt(res["bearing"],2))
                    lines.append('<div class="kv"><span class="k">Δquota</span><span class="v">%s m</span></div>' % fmt(res["Δquota"],4))
                    lines.append('</div>')
                    result_html = "\n".join(lines)
            except Exception as e:
                result_html = f'<div class="card"><b>Errore:</b> {e}</div>'

    return render_template('rtk_compare.html', options=options, result=result_html)
