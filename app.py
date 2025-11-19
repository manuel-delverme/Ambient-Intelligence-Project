"""Minimal HTTP server exposing a web UI for the HVAC sizing helpers."""
from __future__ import annotations

import html
import io
import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs

import hvac_core as hvac

ROOT = Path(__file__).parent.resolve()
STATIC_DIR = ROOT / "static"


def _float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_inputs() -> Dict[str, Any]:
    return {
        "it_load_kW": 400.0,
        "num_whitespaces": 2,
        "row_redundancy": "6+2",
        "crah_redundancy": "3+1",
        "pump_redundancy": "3+1",
        "chiller_redundancy": "3+1",
        "string_redundancy": "3+1",
        "dt_air": 10.0,
        "sp_air": 300.0,
        "eta_fan": 0.6,
        "eta_motor": 0.95,
        "dt_water": 6.0,
        "pump_head": 30.0,
        "eta_pump": 0.75,
        "eta_pump_motor": 0.95,
        "cop_chiller": 5.0,
    }


def _summarize_rows(rows):
    return [
        {
            "label": f"ITR{row.id}",
            "whitespace": row.whitespace_id,
            "load_kW": row.normal_kW,
        }
        for row in rows
    ]


def _format_whitespaces(white_spaces):
    return [
        {
            "id": ws.id,
            "load_kW": ws.it_load_kW,
            "row_ids": ws.row_ids,
        }
        for ws in white_spaces
    ]


def _format_report(report: hvac.PowerStringReport):
    table = []
    for row in report.table:
        table.append(
            {
                "string_id": row.string_id,
                "normal_load_kW": row.normal_load_kW,
                "design_capacity_kW": row.design_capacity_kW,
                "utilization": row.utilization,
                "components": [
                    {
                        "label": comp.label,
                        "kind": comp.kind,
                        "normal_kW": comp.normal_kW,
                        "capacity_kW": comp.capacity_kW,
                        "whitespace_id": comp.whitespace_id,
                        "primary_string": comp.primary_string,
                        "secondary_string": comp.secondary_string,
                        "dual_fed": comp.dual_fed,
                    }
                    for comp in row.components
                ],
            }
        )
    return {
        "table": table,
        "failure_cases": report.failure_cases,
    }


def run_simulation(form_data: Dict[str, Any]):
    inputs = default_inputs()
    for key, value in form_data.items():
        inputs[key] = value if value != [""] else ""

    inputs["it_load_kW"] = _float(inputs["it_load_kW"], 0.0)
    inputs["num_whitespaces"] = _int(inputs["num_whitespaces"], 1)
    inputs["dt_air"] = _float(inputs["dt_air"], 10.0)
    inputs["sp_air"] = _float(inputs["sp_air"], 300.0)
    inputs["eta_fan"] = _float(inputs["eta_fan"], 0.6)
    inputs["eta_motor"] = _float(inputs["eta_motor"], 0.95)
    inputs["dt_water"] = _float(inputs["dt_water"], 6.0)
    inputs["pump_head"] = _float(inputs["pump_head"], 30.0)
    inputs["eta_pump"] = _float(inputs["eta_pump"], 0.75)
    inputs["eta_pump_motor"] = _float(inputs["eta_pump_motor"], 0.95)
    inputs["cop_chiller"] = _float(inputs["cop_chiller"], 5.0)

    row_cfg = hvac.parse_redundancy(inputs["row_redundancy"])
    crah_cfg = hvac.parse_redundancy(inputs["crah_redundancy"])
    pump_cfg = hvac.parse_redundancy(inputs["pump_redundancy"])
    chiller_cfg = hvac.parse_redundancy(inputs["chiller_redundancy"])
    string_cfg = hvac.parse_redundancy(inputs["string_redundancy"])

    whitespaces, it_rows = hvac.distribute_it_load(
        inputs["it_load_kW"], inputs["num_whitespaces"], row_cfg
    )

    crah_units, Q_crah_out, _ = hvac.size_crah(
        inputs["it_load_kW"],
        inputs["dt_air"],
        inputs["sp_air"],
        inputs["eta_fan"],
        inputs["eta_motor"],
        crah_cfg,
        white_spaces=whitespaces,
    )

    pump_units, Q_pump_out, _ = hvac.size_pumps(
        Q_crah_out,
        inputs["dt_water"],
        inputs["pump_head"],
        inputs["eta_pump"],
        inputs["eta_pump_motor"],
        pump_cfg,
    )

    chiller_units, Q_cond, _ = hvac.size_chillers(
        Q_pump_out,
        inputs["cop_chiller"],
        chiller_cfg,
    )

    aggregate = hvac.aggregate_power_strings(
        crah_units=crah_units,
        pump_units=pump_units,
        chiller_units=chiller_units,
        it_rows=it_rows,
        redundancy_strings=string_cfg,
    )

    report = hvac.build_power_string_report(aggregate)

    return {
        "inputs": inputs,
        "white_spaces": _format_whitespaces(whitespaces),
        "it_rows": _summarize_rows(it_rows),
        "aggregate": aggregate,
        "report": _format_report(report),
        "Q_crah_out": Q_crah_out,
        "Q_pump_out": Q_pump_out,
        "Q_ch_cond": Q_cond,
    }


def _render_list(items):
    return "".join(f"<li>{item}</li>" for item in items)


def _render_table_rows(rows, columns):
    body = []
    for row in rows:
        body.append("<tr>" + "".join(columns(row)) + "</tr>")
    return "".join(body)


def build_page(data):
    def inp(name):
        value = data["inputs"].get(name, "")
        if isinstance(value, float):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return html.escape(str(value))

    ws_rows = _render_table_rows(
        data["white_spaces"],
        lambda ws: [
            f"<td>{ws['id']}</td>",
            f"<td>{ws['load_kW']:.1f}</td>",
            f"<td>{', '.join(map(str, ws['row_ids']))}</td>",
        ],
    )

    row_rows = _render_table_rows(
        data["it_rows"],
        lambda row: [
            f"<td>{row['label']}</td>",
            f"<td>{row['whitespace']}</td>",
            f"<td>{row['load_kW']:.1f}</td>",
        ],
    )

    string_rows = []
    dual_feed_rows = []
    for row in data["report"]["table"]:
        comps = []
        for comp in row["components"]:
            feed_bits = []
            if comp["primary_string"]:
                feed_bits.append(f"Primary S{comp['primary_string']}")
            if comp["secondary_string"]:
                feed_bits.append(f"Secondary S{comp['secondary_string']}")
            else:
                feed_bits.append("Single feed")
            comps.append(
                "<li>"
                + f"<strong>{html.escape(comp['label'])}</strong> ({comp['kind']}) – {comp['normal_kW']:.1f} kW"
                + (f" · cap {comp['capacity_kW']:.1f} kW" if comp["capacity_kW"] else "")
                + (f" · WS {comp['whitespace_id']}" if comp["whitespace_id"] else "")
                + (" · " + " / ".join(feed_bits) if feed_bits else "")
                + "</li>"
            )
            dual_feed_rows.append(
                {
                    "label": comp["label"],
                    "kind": comp["kind"],
                    "primary": comp["primary_string"],
                    "secondary": comp["secondary_string"],
                    "dual": comp["dual_fed"],
                }
            )
        comps_html = "".join(comps)
        string_rows.append(
            "<tr>"
            f"<td>{row['string_id']}</td>"
            f"<td>{row['normal_load_kW']:.1f}</td>"
            f"<td>{row['design_capacity_kW']:.1f}</td>"
            f"<td>{row['utilization'] * 100:.1f}%</td>"
            f"<td><ul>{comps_html}</ul></td>"
            "</tr>"
        )

    dual_section = ""
    if dual_feed_rows:
        dual_section = (
            "<section class='card'>"
            "<h2>Feed assignments</h2>"
            "<table><thead><tr><th>Component</th><th>Type</th><th>Primary string</th><th>Secondary string</th><th>Dual fed?</th></tr></thead>"
            "<tbody>"
            + "".join(
                "<tr>"
                f"<td>{html.escape(row['label'])}</td>"
                f"<td>{row['kind']}</td>"
                f"<td>{row['primary'] if row['primary'] else '–'}</td>"
                f"<td>{row['secondary'] if row['secondary'] else '–'}</td>"
                f"<td>{'Yes' if row['dual'] else 'No'}</td>"
                "</tr>"
                for row in dual_feed_rows
            )
            + "</tbody></table></section>"
        )

    failure_section = ""
    failure_cases = data["report"].get("failure_cases") or []
    if failure_cases:
        failure_rows = []
        for case in failure_cases:
            loads = "".join(
                f"<li>String {sid}: {load:.1f} kW</li>"
                for sid, load in sorted(case.get("redistributed_normal_kW", {}).items())
            )
            lost = case.get("lost_units") or []
            lost_text = ", ".join(map(html.escape, lost)) if lost else "None"
            failure_rows.append(
                "<tr>"
                f"<td>{case.get('failed_string')}</td>"
                f"<td>{case.get('design_capacity_per_string_kW', 0.0):.1f}</td>"
                f"<td><ul>{loads}</ul></td>"
                f"<td>{lost_text}</td>"
                "</tr>"
            )
        failure_section = (
            "<section class='card failure'>"
            "<h2>Failure scenarios</h2>"
            "<p>Each row shows the impact of losing the selected string. Loads automatically shift to the configured secondary feeds or the least-loaded survivors.</p>"
            "<table>"
            "<thead><tr><th>Failed string</th><th>New design capacity (kW)</th><th>Redistributed loads</th><th>Lost units</th></tr></thead>"
            f"<tbody>{''.join(failure_rows)}</tbody>"
            "</table>"
            "</section>"
        )

    summary_cards = f"""
        <section class="grid summary">
          <article class="card">
            <h3>Whitespace distribution</h3>
            <table><thead><tr><th>ID</th><th>IT load (kW)</th><th>Row IDs</th></tr></thead>
            <tbody>{ws_rows}</tbody></table>
          </article>
          <article class="card">
            <h3>IT rows</h3>
            <table><thead><tr><th>Row</th><th>Whitespace</th><th>Load (kW)</th></tr></thead>
            <tbody>{row_rows}</tbody></table>
          </article>
          <article class="card">
            <h3>Thermal cascade</h3>
            <ul>
              <li>Total CRAH outlet load: <strong>{data['Q_crah_out']:.1f} kW</strong></li>
              <li>Total pump outlet load: <strong>{data['Q_pump_out']:.1f} kW</strong></li>
              <li>Total chiller condenser load: <strong>{data['Q_ch_cond']:.1f} kW</strong></li>
            </ul>
          </article>
        </section>
    """

    page = f"""
    <!doctype html>
    <html lang='en'>
    <head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width, initial-scale=1'>
      <title>HVAC Power String Explorer</title>
      <link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap'>
      <link rel='stylesheet' href='/static/style.css'>
    </head>
    <body>
      <main>
        <header>
          <div>
            <h1>HVAC Power String Explorer</h1>
            <p>Adjust inputs, size the mechanical chain, and inspect balanced power strings or failure scenarios.</p>
          </div>
          <form method='post' class='card controls'>
            <h2>Inputs</h2>
            <div class='grid'>
              <label>IT load (kW)<input type='number' step='0.1' name='it_load_kW' value='{inp("it_load_kW")}'></label>
              <label>White spaces<input type='number' min='1' name='num_whitespaces' value='{inp("num_whitespaces")}'></label>
              <label>IT row redundancy<input type='text' name='row_redundancy' value='{inp("row_redundancy")}'></label>
              <label>CRAH redundancy<input type='text' name='crah_redundancy' value='{inp("crah_redundancy")}'></label>
              <label>Pump redundancy<input type='text' name='pump_redundancy' value='{inp("pump_redundancy")}'></label>
              <label>Chiller redundancy<input type='text' name='chiller_redundancy' value='{inp("chiller_redundancy")}'></label>
              <label>String redundancy<input type='text' name='string_redundancy' value='{inp("string_redundancy")}'></label>
              <label>Air ΔT (K)<input type='number' step='0.1' name='dt_air' value='{inp("dt_air")}'></label>
              <label>Static pressure (Pa)<input type='number' step='1' name='sp_air' value='{inp("sp_air")}'></label>
              <label>Fan η<input type='number' step='0.01' name='eta_fan' value='{inp("eta_fan")}'></label>
              <label>Motor η<input type='number' step='0.01' name='eta_motor' value='{inp("eta_motor")}'></label>
              <label>Water ΔT (K)<input type='number' step='0.1' name='dt_water' value='{inp("dt_water")}'></label>
              <label>Pump head (m)<input type='number' step='0.1' name='pump_head' value='{inp("pump_head")}'></label>
              <label>Pump η<input type='number' step='0.01' name='eta_pump' value='{inp("eta_pump")}'></label>
              <label>Pump motor η<input type='number' step='0.01' name='eta_pump_motor' value='{inp("eta_pump_motor")}'></label>
              <label>Chiller COP<input type='number' step='0.1' name='cop_chiller' value='{inp("cop_chiller")}'></label>
            </div>
            <button type='submit'>Recalculate</button>
          </form>
        </header>
        {summary_cards}
        {dual_section}
        <section class='card'>
          <h2>Power string summary</h2>
          <table>
            <thead><tr><th>String</th><th>Normal load (kW)</th><th>Design capacity (kW)</th><th>Utilization</th><th>Components</th></tr></thead>
            <tbody>{''.join(string_rows)}</tbody>
          </table>
        </section>
        {failure_section}
      </main>
    </body>
    </html>
    """
    return page


class HVACHandler(SimpleHTTPRequestHandler):
    def _serve_html(self, content: str):
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/static/"):
            return super().do_GET()
        if self.path == "/report.json":
            data = run_simulation({})
            payload = json.dumps(
                {
                    "aggregate": {
                        "total_peak_kW": data["aggregate"].total_peak_kW,
                        "total_normal_kW": data["aggregate"].total_normal_kW,
                    },
                    "strings": [
                        {
                            "id": s.id,
                            "normal_load_kW": s.normal_load_kW,
                            "design_capacity_kW": s.design_capacity_kW,
                            "units": s.unit_ids,
                        }
                        for s in data["aggregate"].strings
                    ],
                }
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        result = run_simulation({})
        self._serve_html(build_page(result))

    def do_POST(self):
        if not self.path.startswith("/"):
            return super().do_POST()
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        params = parse_qs(body.decode("utf-8"))
        result = run_simulation(params)
        self._serve_html(build_page(result))

    def translate_path(self, path: str) -> str:
        if path.startswith("/static/"):
            return str(STATIC_DIR / path.split("/static/", 1)[1])
        return super().translate_path(path)


def run(host: str = "0.0.0.0", port: int = 5000):
    server = ThreadingHTTPServer((host, port), HVACHandler)
    print(f"Serving HVAC UI at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
