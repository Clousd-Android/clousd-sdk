"""Walk the screens of an app and write a map of it plus a draft app pack (named elements and likely popups).

    from clousd.explore import explore
    graph = explore(phone, "com.android.settings", depth=2)

Breadth-first: open the app, read the screen, tap each labelled element in turn, read what opened, go back; screens
are told apart by the activity and the set of labels on them. Elements whose label sounds destructive or
outward-facing (delete, send, pay, sign out, ...) are never tapped. After every step back the walker checks it is
where it should be; if not, it reopens the app and replays the path from the start.

The result: {"package", "screens": [{id, activity, labels, elements}], "edges": [{from, to, via}],
"app_pack": {"package", "elements": {name: [selector, ...]}, "popups": [...]}} - the app pack is a draft for
gateway recipes (selectors by resource-id, then by text), to be reviewed by a person.
"""
from __future__ import annotations

import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from .client import ClousdError, Device

# Words that mean an action with consequences: the walker never taps an element whose text (or any text drawn inside
# it) contains one of them as a whole word. English plus the Latin-script languages a phone's exit country commonly
# sets (German, French, Spanish, Portuguese, Italian, Indonesian, Vietnamese). Other scripts: explore only with the
# phone set to one of these languages, or review the map before trusting it.
_DANGER_WORDS = (
    # destroy / leave
    "delete remove erase reset wipe format uninstall disable deactivate deregister unregister unsubscribe unfollow unfriend "
    "leave archive clear forget disconnect logout sign-out signout log-out power-off restart reboot factory "
    # money / outward
    "buy pay purchase subscribe order checkout cart donate tip transfer withdraw deposit send post publish share upload "
    "submit call dial report block flag invite "
    # confirmations (a dialog opened by a safe tap must not be confirmed)
    "ok okay yes confirm continue accept agree allow enable install apply save done proceed next "
    # de
    "löschen entfernen zurücksetzen deinstallieren abmelden kaufen bezahlen senden teilen veröffentlichen bestätigen "
    "weiter akzeptieren erlauben anrufen melden blockieren "
    # fr
    "supprimer effacer réinitialiser désinstaller déconnexion déconnecter acheter payer envoyer partager publier "
    "confirmer continuer accepter autoriser appeler signaler bloquer "
    # es
    "eliminar borrar restablecer desinstalar salir comprar pagar enviar compartir publicar confirmar continuar aceptar "
    "permitir llamar denunciar reportar bloquear "
    # pt
    "excluir apagar redefinir desinstalar sair comprar pagar enviar compartilhar partilhar publicar confirmar continuar "
    "aceitar permitir ligar denunciar bloquear "
    # it
    "elimina cancella ripristina disinstalla esci acquista paga invia condividi pubblica conferma continua accetta "
    "consenti chiama segnala blocca "
    # id
    "hapus setel-ulang keluar beli bayar kirim bagikan terbitkan konfirmasi lanjutkan terima izinkan telepon laporkan blokir "
    # vi
    "xóa xoá đặt-lại gỡ đăng-xuất mua thanh-toán gửi chia-sẻ đăng xác-nhận tiếp-tục chấp-nhận cho-phép gọi báo-cáo chặn"
).split()
_DANGER = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(w).replace(r"\-", "[ -]?") for w in _DANGER_WORDS) + r")(?![\w-])", re.I)
_DANGER_PHRASES = re.compile(r"turn off|switch off|clear (data|storage|cache|history|browsing)|force stop|sign out|log out|"
                             r"add to (cart|basket)|check out|developer options", re.I)
POPUP = re.compile(r"^(not now|skip|cancel|no thanks|maybe later|close|dismiss|got it|ok|allow|deny|don.t allow|later)$", re.I)
CONFIRM = re.compile(r"^(ok|okay|yes|confirm|continue|accept|agree|allow|enable|turn on|turn off|disable|force stop|delete|remove|"
                     r"uninstall|sign out|log out|cancel|no|not now|don.t allow|deny)$", re.I)


def dangerous(text: str) -> bool:
    """True when a label (its own text, or any text inside the tapped element) names an action with consequences."""
    t = text.replace("_", " ")
    return bool(_DANGER.search(t) or _DANGER_PHRASES.search(t))


def _inside(a: List[int], b: List[int]) -> bool:
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _subtree_text(o: Dict[str, Any], n: Dict[str, Any]) -> str:
    """The element's own label and id plus every label drawn inside its bounds: the text of a clickable row usually
    sits on a child view."""
    parts = [_label(n), _rid(n)]
    b = n.get("b")
    if b:
        for m in o.get("ui", []):
            mb = m.get("b")
            if mb and m is not n and _inside(mb, b):
                parts.append(_label(m))
                parts.append(_rid(m))
    return " ".join(p for p in parts if p)


def is_dialog(o: Dict[str, Any]) -> bool:
    """A small screen whose buttons are confirmations: a dialog. The walker never presses its buttons."""
    labs = [_label(m) for m in o.get("ui", []) if _label(m)]
    btns = [x for x in labs if CONFIRM.match(x)]
    return 0 < len(btns) <= 3 and len(labs) <= 10


def _label(n: Dict[str, Any]) -> str:
    return (n.get("text") or n.get("desc") or "").strip()


def _rid(n: Dict[str, Any]) -> str:
    return (n.get("id") or "").split("/")[-1]


def _sig(o: Dict[str, Any]) -> str:
    labels = sorted({_label(n) for n in o.get("ui", []) if _label(n) and (n.get("click") or n.get("scroll"))})[:40]
    return (o.get("activity") or "") + "|" + "|".join(labels)


def _clickables(o: Dict[str, Any], pkg: str) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for n in o.get("ui", []):
        if not n.get("click") or n.get("disabled"):
            continue
        if n.get("pkg") and n.get("pkg") != pkg:
            continue
        lab, rid = _label(n), _rid(n)
        key = lab or rid
        if not key or key in seen or dangerous(_subtree_text(o, n)):
            continue
        seen.add(key)
        out.append(n)
    return out


def _selector(n: Dict[str, Any], o: Dict[str, Any]) -> Dict[str, Any]:
    rid = _rid(n)
    if rid and sum(1 for m in o.get("ui", []) if _rid(m) == rid) == 1:
        return {"id": rid}
    lab = _label(n)
    if lab and len(lab) <= 40:
        return {"text": lab}
    return {"id": rid} if rid else {}


def _name(sel: Dict[str, Any]) -> str:
    v = sel.get("id") or sel.get("text") or "el"
    return (re.sub(r"[^a-z0-9]+", "_", str(v).lower()).strip("_") or "el")[:32]


def explore(d: Device, package: str, depth: int = 2, max_screens: int = 25, per_screen: int = 8,
            log=print) -> Dict[str, Any]:
    def say(s: str) -> None:
        if log:
            log(s)

    def look() -> Dict[str, Any]:
        for _ in range(4):
            try:
                o = d.observe(width=360, ui=True)
                if not o.get("ui_error", "").startswith("not_ready"):
                    return o
            except ClousdError as e:
                if e.status not in (409, 502, 503):
                    raise
            time.sleep(3)
        return d.observe(width=360, ui=True)

    def reopen() -> Dict[str, Any]:
        d.action("close_app", package=package)
        time.sleep(1)
        d.act("open_app", package=package)
        time.sleep(2)
        return look()

    def tap(n: Dict[str, Any]) -> None:
        b = n["b"]
        d.act("tap", x=(b[0] + b[2]) // 2, y=(b[1] + b[3]) // 2)

    def find(o: Dict[str, Any], sel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for n in o.get("ui", []):
            if sel.get("id") and _rid(n) == sel["id"]:
                return n
            if sel.get("text") and _label(n) == sel["text"]:
                return n
        return None

    def replay(path: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        o = reopen()
        for sel in path:
            n = find(o, sel)
            if n is None:
                return None
            tap(n)
            o = look()
        return o

    screens: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    elements: Dict[str, List[Dict[str, Any]]] = {}
    popups: List[Dict[str, Any]] = []
    root = reopen()
    rs = _sig(root)
    screens[rs] = {"id": 0, "activity": root.get("activity"), "path": [], "labels": [], "elements": []}
    q: deque = deque([(rs, [])])
    while q and len(screens) < max_screens:
        sig, path = q.popleft()
        if len(path) >= depth:
            continue
        o = replay(path) if path else reopen()
        if o is None or _sig(o) != sig:
            say(f"  cannot return to screen {screens[sig]['id']} - skipped")
            continue
        sc = screens[sig]
        sc["labels"] = sorted({_label(n) for n in o.get("ui", []) if _label(n)})[:80]
        if is_dialog(o):
            say(f"screen {sc['id']}: a dialog - its buttons are not explored")
            d.act("key", key="back")
            continue
        cands = _clickables(o, package)[:per_screen]
        say(f"screen {sc['id']} {o.get('activity')}: {len(cands)} elements to try")
        for n in cands:
            sel = _selector(n, o)
            if not sel:
                continue
            nm = _name(sel)
            elements.setdefault(nm, [sel])
            sc["elements"].append({"name": nm, **sel, "class": n.get("class", "")})
            tap(n)
            o2 = look()
            s2 = _sig(o2)
            if (o2.get("package") or "") != package:
                say(f"  {nm}: left the app ({o2.get('package')})")
            elif s2 != sig:
                labs = [_label(m) for m in o2.get("ui", []) if _label(m)]
                if len(labs) <= 8 and any(POPUP.match(x) for x in labs):
                    btn = next(x for x in labs if POPUP.match(x))
                    popups.append({"name": _name({"text": btn}) + "_after_" + nm, "any": [{"text": btn}], "tap": {"text": btn},
                                   "_seen": " | ".join(labs)[:200]})
                if s2 not in screens:
                    screens[s2] = {"id": len(screens), "activity": o2.get("activity"), "path": path + [sel], "labels": [], "elements": []}
                    q.append((s2, path + [sel]))
                    say(f"  {nm} -> new screen {screens[s2]['id']} {o2.get('activity')}")
                edges.append({"from": sc["id"], "to": screens[s2]["id"], "via": nm})
            # back to this screen
            d.act("key", key="back")
            o3 = look()
            if _sig(o3) != sig:
                o3 = replay(path) if path else reopen()
                if o3 is None or _sig(o3) != sig:
                    say("  lost the way back - next screen")
                    break
            o = o3
            if len(screens) >= max_screens:
                break
    d.action("close_app", package=package)
    return {
        "package": package,
        "screens": sorted(({k: v for k, v in s.items()} for s in screens.values()), key=lambda s: s["id"]),
        "edges": edges,
        "app_pack": {"package": package, "_comment": "draft from clousd explore: review names and selectors before use",
                     "elements": elements, "popups": popups},
    }
