#!/usr/bin/env python3
import re, ast, sys
from pathlib import Path

# Encuentra attrs=" {...} " preservando comillas y espacios
ATTRS_RE = re.compile(r'attrs\s*=\s*([\'"])(.*?)\1', re.DOTALL)

OP_MAP = {
    '=': '==',
    '!=': '!=',
    '>': '>',
    '<': '<',
    '>=': '>=',
    '<=': '<=',
    'in': 'in',
    'not in': 'not in',
    # Odoo a veces usa '=' con listas para 'in'
}

def tokenise(domain):
    """Convierte una lista/domain de Odoo (literal Python) en una secuencia de tokens."""
    if isinstance(domain, (list, tuple)):
        for item in domain:
            yield from tokenise(item)
    elif isinstance(domain, str) and domain in ('&', '|', '!'):
        yield domain
    else:
        yield domain

def to_expr_from_domain(domain):
    """
    Convierte un domain estilo Odoo a expresión Python infija.
    Soporta &, |, ! en notación prefija y tuplas (fld, op, val).
    """
    # Usamos un parser recursivo de notación prefija sobre una lista lineal de tokens
    tokens = list(tokenise(domain))
    # Re-construye en prefijo real
    def build(it):
        try:
            t = next(it)
        except StopIteration:
            raise ValueError("Domain mal formado")
        if t == '&':
            a = build(it); b = build(it)
            return f"({a} and {b})"
        if t == '|':
            a = build(it); b = build(it)
            return f"({a} or {b})"
        if t == '!':
            a = build(it)
            return f"(not ({a}))"
        if (isinstance(t, (list, tuple))
            and len(t) in (2,3)
            and isinstance(t[0], str)):
            # ('field','=',value)  o ('field','=',) rarezas
            fld = t[0]
            if len(t) == 2:
                # ('field', value) → equivale a '='
                op, val = '=', t[1]
            else:
                op, val = str(t[1]).strip(), t[2]
            # Si valor es lista y op '=', úsalo como 'in'
            if op == '=' and isinstance(val, (list, tuple)):
                op = 'in'
            pyop = OP_MAP.get(op)
            if pyop is None:
                raise ValueError(f"Operador no soportado: {op}")
            # Serializa valor como literal Python válido
            val_src = repr(val)
            # Para relacionales en vistas, el valor real es id/ids; asumimos correcto
            if pyop in ('in','not in'):
                return f"({fld} {pyop} {val_src})"
            else:
                return f"({fld} {pyop} {val_src})"
        # Si llega aquí, es un literal booleano suelto
        return repr(t)
    return build(iter(tokens))

def merge_attr(existing: str, new_expr: str):
    """Combina un atributo existente con OR lógico si ya hay algo. Ajusta según necesidad."""
    e = existing.strip()
    if not e:
        return new_expr
    # Conservador: AND para required/readonly/invisible añade restricciones
    return f"({e}) or ({new_expr})" if 'invisible' in '__key__' else f"({e}) or ({new_expr})"

def process_file(p: Path):
    src = p.read_text(encoding="utf-8", errors="ignore")
    changed = False
    out = []
    last_end = 0
    for m in ATTRS_RE.finditer(src):
        out.append(src[last_end:m.start()])
        quote = m.group(1)
        payload = m.group(2).strip()
        try:
            # Carga dict literal con ast.literal_eval
            d = ast.literal_eval(payload)
            if not isinstance(d, dict):
                raise Exception("attrs no es dict")
        except Exception:
            # Deja el attrs tal cual si no parsea
            out.append(m.group(0))
            last_end = m.end()
            continue

        # Genera expr por cada clave conocida
        new_attrs = []
        for key in ('invisible','readonly','required'):
            if key in d:
                try:
                    expr = to_expr_from_domain(d[key])
                    new_attrs.append((key, expr))
                except Exception:
                    # si falla, conserva attrs original
                    new_attrs = None
                    break
        if new_attrs is None or not new_attrs:
            out.append(m.group(0))  # no transformación
        else:
            # Inserta los nuevos atributos y elimina attrs
            # Nota: si ya existen esos atributos en el nodo, no los conocemos aquí.
            # Resolveremos con una pasada extra para evitar duplicados simples.
            repl = " ".join(f'{k}="{v}"' for k,v in new_attrs)
            out.append(repl)
            changed = True
        last_end = m.end()
    out.append(src[last_end:])

    new_src = "".join(out)
    if changed:
        # Elimina duplicados triviales del tipo invisible="" repetido en el mismo tag
        # (simple heurística)
        new_src = re.sub(r'\sattrs\s*=\s*([\'"])(.*?)\1', '', new_src, flags=re.DOTALL)
        p.write_text(new_src, encoding="utf-8")
    return changed

def main():
    if len(sys.argv) != 2:
        print("Uso: python odoo17_attrs_translate.py RUTA_ADDONS")
        sys.exit(1)
    root = Path(sys.argv[1]).resolve()
    if not root.exists():
        print("Ruta no encontrada")
        sys.exit(1)
    files = list(root.rglob("*.xml"))
    touched = 0
    for f in files:
        try:
            if process_file(f):
                touched += 1
        except Exception as e:
            print(f"[WARN] {f}: {e}")
    print(f"Archivos modificados: {touched}/{len(files)}")

if __name__ == "__main__":
    main()
