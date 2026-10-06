"""TechNest Electronics - autonomous escalation agent.
Run:  pip install flask anthropic
      export ANTHROPIC_API_KEY=sk-...   (optional: without it a rule-based agent runs instead)
      python app.py    ->  http://localhost:5000
"""
import os, json, time, uuid, threading
from flask import Flask, request, jsonify, abort

MODEL = os.getenv("MODEL", "claude-sonnet-5-5")
try:
    import anthropic
    client = anthropic.Anthropic() if os.getenv("ANTHROPIC_API_KEY") else None
except ImportError:
    client = None

# ---------- Hierarchy & powers (the "rulebook" the code enforces) ----------
LEVELS = {
    1: dict(name="Rohan Verma", post="Sales Associate", limit=2000, window=7, voucher=0, accessory=200, discount=0),
    2: dict(name="Priya Nair", post="Store Supervisor", limit=15000, window=15, voucher=0, accessory=200, discount=10),
    3: dict(name="Arjun Mehta", post="Store Manager", limit=100000, window=3650, voucher=5000, accessory=200, discount=10),
}
MAX_REJECTS = 2  # proposals the customer may reject at one level before escalation

def powers(l):
    p = LEVELS[l]
    win = f"{p['window']} days old" if p["window"] < 999 else "any age"
    return (f"Refund/replacement only if order value <= Rs{p['limit']:,} and purchase is {('<= ' + win) if p['window']<999 else win}. "
            f"Free accessory up to Rs{p['accessory']}. Voucher up to Rs{p['voucher']}. Discount on next purchase up to {p['discount']}%. "
            f"Priority repair with free pickup and fee waiver: {'yes' if l >= 2 else 'no'}.")

def check(l, actions, c):
    """Hard guardrail: returns an error string if the offer exceeds this level's power."""
    p = LEVELS[l]
    for a in actions:
        t, v = a["type"], a.get("value", 0) or 0
        if t in ("refund", "replacement"):
            if c["amount"] > p["limit"]:
                return f"order value Rs{c['amount']:,.0f} exceeds my Rs{p['limit']:,} limit"
            if c["days"] > p["window"]:
                return f"purchase is {c['days']} days old, beyond my {p['window']}-day return window"
        elif t == "repair" and v and l < 2: return "priority repair needs supervisor authority"
        elif t == "waive_fees" and l < 2: return "fee waivers need supervisor authority"
        elif t == "voucher" and v > p["voucher"]: return f"voucher of Rs{v:,.0f} exceeds my limit of Rs{p['voucher']}"
        elif t == "accessory" and v > p["accessory"]: return "accessory value exceeds my limit"
        elif t == "discount" and v > p["discount"]: return f"{v:g}% discount exceeds my {p['discount']}% limit"
        elif t not in ("repair", "voucher", "accessory", "discount", "waive_fees", "apology", "refund", "replacement"):
            return f"unknown action {t}"
    return None

def fmt(a, c):
    t, v = a["type"], a.get("value", 0) or 0
    return {"refund": f"Full refund of Rs{c['amount']:,.0f}", "replacement": "Free replacement",
            "repair": "Priority repair + free pickup/drop" if v else "Standard repair ticket",
            "voucher": f"Rs{v:,.0f} voucher", "accessory": f"Free accessory (Rs{v:,.0f})",
            "discount": f"{v:g}% off your next purchase", "waive_fees": "Service/restocking fees waived",
            "apology": "Formal apology"}.get(t, t)

# ---------- The decision makers ----------
TOOLS = [
    {"name": "propose", "description": "Offer the customer a resolution using only your own powers.",
     "input_schema": {"type": "object", "properties": {
         "actions": {"type": "array", "items": {"type": "object", "properties": {
             "type": {"type": "string", "enum": ["refund", "replacement", "repair", "voucher", "accessory", "discount", "waive_fees", "apology"]},
             "value": {"type": "number", "description": "voucher/accessory: rupees; discount: percent; repair: 1=priority+free pickup, 0=standard"}},
             "required": ["type"]}},
         "message": {"type": "string", "description": "Short, empathetic message to the customer"}},
         "required": ["actions", "message"]}},
    {"name": "escalate", "description": "Hand the case to your senior when it needs power you lack or your offers failed.",
     "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}},
]

def llm_decide(c, l):
    p = LEVELS[l]
    system = (f"You are {p['name']}, {p['post']} at TechNest Electronics. Resolve the complaint using ONLY your powers: {powers(l)} "
              "Be empathetic; propose the fairest, least costly fix. Never repeat a rejected offer - improve it within your powers "
              "(the customer's comments say what they want). If the fix needs power you lack, call escalate. Otherwise call propose.")
    ctx = json.dumps({**{k: c[k] for k in ("customer", "category", "description", "amount", "days")}, "rejected_offers": c["rejected"]})
    r = client.messages.create(model=MODEL, max_tokens=500, system=system, tools=TOOLS,
                               tool_choice={"type": "any"}, messages=[{"role": "user", "content": ctx}])
    b = next(x for x in r.content if x.type == "tool_use")
    if b.name == "escalate":
        return {"action": "escalate", "reason": b.input["reason"]}
    return {"action": "propose", "actions": b.input["actions"], "message": b.input["message"]}

BASE = {"defective product": ["replacement", "refund"], "wrong item": ["replacement", "refund"],
        "billing error": ["refund", "refund"], "delayed repair or delivery": ["repair", "refund"],
        "warranty dispute": ["repair", "replacement"], "staff behavior": ["apology", "voucher"]}

def mock_decide(c, l):
    """Offline rule-based stand-in so the whole flow works without an API key."""
    n = sum(1 for r in c["rejected"] if r["level"] == l)
    t = BASE.get(c["category"], ["replacement", "refund"])[min(n, 1)]
    acts = [{"type": t, "value": 1 if (t == "repair" and l >= 2) else 0}]
    if n >= 1:  # sweeten the second offer with the best extra this level allows
        if l == 3: acts.append({"type": "voucher", "value": 500})
        elif l == 2: acts.append({"type": "waive_fees"})
        else: acts.append({"type": "accessory", "value": 200})
    return {"action": "propose", "actions": acts, "message": f"I'm sorry about this. Here's what I can do for you."}

def decide(c, l):
    try:
        return llm_decide(c, l) if client else mock_decide(c, l)
    except Exception as e:  # agent failure / timeout counts as "level didn't act" -> escalate
        return {"action": "escalate", "reason": f"agent unavailable ({type(e).__name__})"}

# ---------- Autonomous loop ----------
CASES = {}

def log(c, kind, who, text):
    c["log"].append({"t": time.strftime("%H:%M:%S"), "kind": kind, "who": who, "text": text})

def who(l): return f"{LEVELS[l]['name']} ({LEVELS[l]['post']})"

def escalate(c, reason):
    l = c["level"]
    if l == 3:
        c["status"] = "human_review"
        log(c, "system", who(3), f"Could not resolve: {reason}. Case sent to head-office human review.")
        return
    c["level"], c["rejects"] = l + 1, 0
    log(c, "system", who(l), f"Escalating to {who(l+1)} - {reason}")

def run(c):
    """Loops by itself until the customer is satisfied or humans take over."""
    while c["status"] == "awaiting_agent":
        l = c["level"]
        if c["rejects"] >= MAX_REJECTS:
            escalate(c, f"customer rejected {MAX_REJECTS} offers"); continue
        d = decide(c, l)
        if d["action"] == "escalate":
            escalate(c, d["reason"]); continue
        err = check(l, d["actions"], c)
        if err:
            escalate(c, err); continue
        c["pending"] = {"actions": [fmt(a, c) for a in d["actions"]], "raw": d["actions"], "message": d["message"]}
        c["status"] = "awaiting_customer"
        log(c, "agent", who(l), d["message"] + " Offer: " + "; ".join(c["pending"]["actions"]))

# ---------- API ----------
app = Flask(__name__)

@app.post("/api/cases")
def new_case():
    j = request.json
    c = dict(id=uuid.uuid4().hex[:8], customer=j.get("customer", "Customer"), category=j["category"],
             description=j["description"], amount=float(j.get("amount") or 0), days=int(j.get("days") or 0),
             level=1, rejects=0, rejected=[], pending=None, status="awaiting_agent", log=[])
    CASES[c["id"]] = c
    log(c, "customer", c["customer"], f"[{c['category']}] {c['description']} (Rs{c['amount']:,.0f}, bought {c['days']} days ago)")
    log(c, "system", "System", f"Case routed to {who(1)}")
    threading.Thread(target=run, args=(c,), daemon=True).start()
    return jsonify(id=c["id"])

@app.get("/api/cases/<cid>")
def get_case(cid):
    return jsonify(CASES.get(cid) or abort(404))

@app.post("/api/cases/<cid>/respond")
def respond(cid):
    c, j = CASES.get(cid) or abort(404), request.json
    if c["status"] != "awaiting_customer":
        return jsonify(error="nothing to respond to"), 400
    l = c["level"]
    if j["accept"]:
        c["status"] = "resolved"
        log(c, "customer", c["customer"], "Accepted the offer. Satisfied.")
        log(c, "system", "System", f"Case RESOLVED by {who(l)}")
    else:
        c["rejects"] += 1
        com = j.get("comment", "").strip()
        c["rejected"].append({"level": l, "offer": c["pending"]["actions"], "comment": com})
        log(c, "customer", c["customer"], "Rejected the offer." + (f" Comment: {com}" if com else ""))
        c["status"], c["pending"] = "awaiting_agent", None
        threading.Thread(target=run, args=(c,), daemon=True).start()
    return jsonify(ok=True)

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>TechNest Support</title><style>
body{font:15px system-ui;max-width:680px;margin:24px auto;padding:0 14px;background:#f5f6f8;color:#222}
.card{background:#fff;border-radius:10px;padding:16px;margin-bottom:14px;box-shadow:0 1px 3px #0002}
input,select,textarea,button{font:inherit;padding:8px;margin:4px 0;width:100%;box-sizing:border-box;border:1px solid #ccc;border-radius:6px}
button{background:#2563eb;color:#fff;border:0;cursor:pointer}button.no{background:#dc2626}button.ok{background:#16a34a}
.row{display:flex;gap:8px}.row>*{flex:1}.ev{padding:8px;border-left:4px solid #bbb;margin:6px 0;background:#fafafa}
.agent{border-color:#2563eb}.customer{border-color:#16a34a}.system{border-color:#f59e0b}.t{color:#888;font-size:12px}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;background:#e5e7eb;font-size:13px}
</style><h2>TechNest Electronics - Customer Support</h2>
<div class=card id=form><b>Raise a complaint</b>
<input id=cu placeholder="Your name">
<select id=cat><option>defective product<option>wrong item<option>billing error<option>delayed repair or delivery<option>warranty dispute<option>staff behavior</select>
<textarea id=desc rows=3 placeholder="Describe the problem"></textarea>
<div class=row><input id=amt type=number placeholder="Order amount (Rs)"><input id=days type=number placeholder="Days since purchase"></div>
<button onclick=submitCase()>Submit complaint</button></div>
<div class=card id=case style=display:none><div id=head></div><div id=log></div><div id=act></div></div>
<script>
let cid=null,last='';const $=i=>document.getElementById(i);const h=s=>{const d=document.createElement('div');d.textContent=s;return d.innerHTML};
async function submitCase(){
 const r=await fetch('/api/cases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({customer:$('cu').value||'Customer',category:$('cat').value,description:$('desc').value,amount:$('amt').value,days:$('days').value})});
 cid=(await r.json()).id;$('form').style.display='none';$('case').style.display='block';poll();}
async function respond(a){await fetch(`/api/cases/${cid}/respond`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({accept:a,comment:($('com')||{}).value||''})});}
async function poll(){
 const c=await (await fetch('/api/cases/'+cid)).json();
 $('head').innerHTML=`<b>Case ${h(c.id)}</b> <span class=badge>${c.status.replace('_',' ')}</span> <span class=badge>Level ${c.level}</span>`;
 $('log').innerHTML=c.log.map(e=>`<div class="ev ${e.kind}"><span class=t>${e.t} - ${h(e.who)}</span><br>${h(e.text)}</div>`).join('');
 if(c.status!=last){last=c.status;$('act').innerHTML=c.status=='awaiting_customer'?`<input id=com placeholder="Optional: tell us what you'd prefer"><div class=row><button class=ok onclick=respond(true)>Accept</button><button class=no onclick=respond(false)>Reject</button></div>`:
  c.status=='awaiting_agent'?'<i>Our team is working on it...</i>':c.status=='resolved'?'<b>Resolved. Thank you!</b>':'<b>A senior executive will contact you personally.</b>';}
 if(!['resolved','human_review'].includes(c.status))setTimeout(poll,1500);}
</script>"""

@app.get("/")
def index():
    return PAGE

if __name__ == "__main__":
    print("Mode:", "Claude agents" if client else "rule-based agents (set ANTHROPIC_API_KEY for AI)")
    app.run(debug=False, port=5000)
