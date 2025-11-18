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
        "failed_string": "",
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
                    }
                    for comp in row.components
                ],
            }
        )
    return {
        "table": table,
        "failure_case": report.failure_case,
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

    failed_string_value = form_data.get("failed_string", [""])
    failed_string_raw = failed_string_value[0] if isinstance(failed_string_value, list) else failed_string_value
    failed_string_idx = _int(failed_string_raw, 0) if failed_string_raw else None

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

    report = hvac.build_power_string_report(aggregate, failed_string_idx)

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
    for row in data["report"]["table"]:
        comps = "".join(
            f"<li><strong>{html.escape(comp['label'])}</strong> ({comp['kind']}) – {comp['normal_kW']:.1f} kW"
            + (f" · cap {comp['capacity_kW']:.1f} kW" if comp["capacity_kW"] else "")
            + (f" · WS {comp['whitespace_id']}" if comp["whitespace_id"] else "")
            + "</li>"
            for comp in row["components"]
        )
        string_rows.append(
            "<tr>"
            f"<td>{row['string_id']}</td>"
            f"<td>{row['normal_load_kW']:.1f}</td>"
            f"<td>{row['design_capacity_kW']:.1f}</td>"
            f"<td>{row['utilization'] * 100:.1f}%</td>"
            f"<td><ul>{comps}</ul></td>"
            "</tr>"
        )

    failure_section = ""
    failure_case = data["report"].get("failure_case")
    if failure_case:
        if failure_case.get("message"):
            failure_section = (
                "<section class='card failure'><h2>Failure scenario</h2>"
                f"<p>{html.escape(failure_case['message'])}</p></section>"
            )
        else:
            rows = "".join(
                f"<tr><td>{sid}</td><td>{load:.1f}</td></tr>"
                for sid, load in failure_case["redistributed_normal_kW"].items()
            )
            lost = failure_case.get("lost_units") or []
            lost_html = (
                f"<p>Units without surviving feeds: {', '.join(map(html.escape, lost))}</p>"
                if lost
                else ""
            )
            failure_section = (
                "<section class='card failure'><h2>Failure scenario</h2>"
                f"<p>Failing string <strong>{failure_case['failed_string']}</strong>. "
                f"New design capacity per surviving string: <strong>{failure_case['design_capacity_per_string_kW']:.1f} kW</strong></p>"
                "<table><thead><tr><th>String</th><th>Redistributed load (kW)</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
                f"{lost_html}</section>"
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
              <label>Failed string<input type='number' min='1' name='failed_string' value='{inp("failed_string")}'></label>
            </div>
            <button type='submit'>Recalculate</button>
          </form>
        </header>
        {summary_cards}
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
