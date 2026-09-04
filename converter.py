"""Конвертер Разметка (Модуль семантического анализа) → Тангл (nanoCAD).

Использование:
    python converter.py <input_markup.json> <output_tangle.json>

Логика:
- obj → find (Aggregates)
- subj → where (там же проверяется параметр)
- class из lvl4 — ключ в Query
- value: ksi → valueMeta.code, custom → value
- Пересчёт единиц (м → мм, м² → мм² и т.д.)
- ColorString по (flavor, opvalue)
- Хэши: SHA-256 от нормализованного JSON
"""

import json
import hashlib
import sys
from datetime import datetime, timezone

# ===== Конфигурация =====

DEFAULTS = {
    "ColorString": "#FF32CD32",          # дефолт; переопределяется по правилу
    "CompanyId": "3da1ee04-dd13-af3c-9fbd-3a0e8208f74c",
    "Type": "control-information",
    "UserId": "00000000-0000-0000-0000-000000000000",
    "IsPrivate": False,
}

ZERO_GUID = "00000000-0000-0000-0000-000000000000"

COLOR_RULES = {
    ("quantitative", "equal"): "#FFEF6C00",
    ("quantitative", "nonEqual"): "#FF32CD32",
    ("qualitative", "*"): "#FFFF1493",
}

OPVALUE_RULES = {
    None: "equal",
    "более": "greater",
    "больше": "greater",
    "менее": "less",
    "меньше": "less",
    "не менее": "greaterOrEqual",
    "не меньше": "greaterOrEqual",
    "не более": "lessOrEqual",
    "не больше": "lessOrEqual",
}

UNITS = {
    "м":    {"standard": "mm",  "factor": 1_000},
    "м²":   {"standard": "mm2", "factor": 1_000_000},
    "м 2":  {"standard": "mm2", "factor": 1_000_000},
    "м2":   {"standard": "mm2", "factor": 1_000_000},
    "м³":   {"standard": "mm3", "factor": 1_000_000_000},
    "м 3":  {"standard": "mm3", "factor": 1_000_000_000},
    "мм":   {"standard": "mm",  "factor": 1},
    "см":   {"standard": "mm",  "factor": 10},
    "км":   {"standard": "mm",  "factor": 1_000_000},
}

PARAM_PREFIX = "#"


# ===== Парсинг разметки =====

def parse_markup(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def index_by_id(nodes):
    return {n["id"]: n for n in nodes}


def build_l1_to_l4_edges(edges):
    """Возвращает dict: l1_id -> l4_id."""
    result = {}
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        if src.startswith("n-1-") and tgt.startswith("n-4-"):
            result[src] = tgt
    return result


def extract_l4_class_and_value(l4_node):
    """Возвращает (class_code, value).

    Логика:
    - custom classifier с classMeta=null: key = data.class (строка типа "ИМЯ")
    - ksi classifier с classMeta: key = classMeta.code (например, "XPG_0013")
    - иначе: key = data.class как есть
    """
    data = l4_node["data"]
    classifier = data.get("classifier")
    values = data.get("values", [])
    class_meta = data.get("classMeta")

    if classifier == "ksi" and class_meta and class_meta.get("code"):
        cls = class_meta["code"]
    else:
        cls = data.get("class")
        if not isinstance(cls, str):
            cls = str(cls)

    if not values:
        return cls, None
    v = values[0]
    if v.get("valueType") == "ksi" and v.get("valueMeta"):
        value = v["valueMeta"].get("code")
    elif v.get("valueType") == "custom":
        value = v.get("value")
    else:
        value = v.get("value")
    return cls, value


def get_l1_role(node):
    return node["data"].get("type")


def get_l2_flavor(node):
    return node["data"].get("flavor")


def get_comparative_text(node):
    """Возвращает текст из токенов lvl1 comparative relation."""
    tokens = node["data"].get("tokens", [])
    return " ".join(t.get("raw", "") for t in tokens).strip() or None


def get_unit_quant_tokens(l4_node):
    """Возвращает список [{quant, unit}] из lvl4."""
    return l4_node["data"].get("unitQuantsTokens", []) or []


def get_classmeta_name(l4_node):
    cm = l4_node["data"].get("classMeta") or {}
    return cm.get("name", l4_node["data"].get("class", ""))


def determine_semantic(sent):
    """Главная функция извлечения семантики из предложения.

    Возвращает dict с полями:
      - object: {class, value}
      - subject: {class, value}
      - flavor: "quantitative" | "qualitative"
      - comparative_text: str | None
      - property_class: str (например, XPG_0013, XPM_0002)
      - property_name: str (например, Толщина)
      - quantity: str
      - unit: str
      - standard_unit: str
      - value_std: int
    """
    nodes = sent["nodes"]
    edges = sent["edges"]
    idx = index_by_id(nodes)
    l1_to_l4 = build_l1_to_l4_edges(edges)

    result = {
        "object": None,
        "subject": None,
        "flavor": None,
        "comparative_text": None,
        "property_class": None,
        "property_name": None,
        "property_value": None,
        "quantity": None,
        "unit": None,
        "standard_unit": None,
        "value_std": None,
    }

    # Сначала находим все lvl1 по ролям
    l1_nodes = [n for n in nodes if n["type"] == "lvl1"]
    for n in l1_nodes:
        role = get_l1_role(n)
        l4_id = l1_to_l4.get(n["id"])
        l4 = idx.get(l4_id) if l4_id else None

        if role == "object" and l4 is not None:
            cls, val = extract_l4_class_and_value(l4)
            result["object"] = {"class": cls, "value": val, "l4_id": l4_id}
        elif role == "subject" and l4 is not None:
            cls, val = extract_l4_class_and_value(l4)
            result["subject"] = {"class": cls, "value": val, "l4_id": l4_id}
        elif role == "property" and l4 is not None:
            cls, _ = extract_l4_class_and_value(l4)
            result["property_class"] = cls
            result["property_name"] = get_classmeta_name(l4)
        elif role == "feature" and l4 is not None:
            cls, val = extract_l4_class_and_value(l4)
            result["property_class"] = cls
            result["property_name"] = get_classmeta_name(l4) or (val or "")
            result["property_value"] = val
        elif role == "comparative relation":
            result["comparative_text"] = get_comparative_text(n)
        elif role == "quantity":
            toks = n["data"].get("tokens", [])
            result["quantity"] = "".join(t.get("raw", "") for t in toks)
        elif role == "units":
            # Берём unitQuantsTokens из связанного property/feature lvl4
            toks = n["data"].get("tokens", [])
            unit_text = "".join(t.get("raw", "") for t in toks)
            result["unit"] = unit_text
            # Если есть связанный property lvl4 с unitQuantsTokens — берём оттуда
            if l4 is not None:
                uq = get_unit_quant_tokens(l4)
                if uq:
                    u = uq[0].get("unit", unit_text)
                    q = uq[0].get("quant", result["quantity"])
                    result["unit"] = u
                    result["quantity"] = q

    # Определяем flavor по lvl2 (берём из требования, не из structural connection)
    for n in nodes:
        if n["type"] == "lvl2":
            flavor = get_l2_flavor(n)
            t = n["data"].get("type")
            if flavor == "requirement":
                result["flavor"] = t  # "quantitative" или "qualitative"
                break

    # Пересчёт единицы
    if result["quantity"] is not None and result["unit"] in UNITS:
        unit_info = UNITS[result["unit"]]
        result["standard_unit"] = unit_info["standard"]
        try:
            result["value_std"] = int(
                float(result["quantity"].replace(",", ".")) * unit_info["factor"]
            )
        except ValueError:
            result["value_std"] = None

    return result


# ===== Построение Tangle =====

def determine_opvalue(comparative_text):
    return OPVALUE_RULES.get(comparative_text, "equal")


def determine_color(flavor, opvalue):
    if flavor == "qualitative":
        return COLOR_RULES[("qualitative", "*")]
    if opvalue == "equal":
        return COLOR_RULES[("quantitative", "equal")]
    return COLOR_RULES[("quantitative", "nonEqual")]


def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def is_ksi_classifier(cls):
    """Возвращает True для ksi-классификаторов (XNKC, XPG, XPM и т.д.)."""
    if not cls:
        return False
    s = str(cls)
    return s.startswith("XNKC") or s.startswith("XPG") or s.startswith("XPM")


def prefix_class(cls):
    """Добавляет префикс # для ksi-классификаторов."""
    if is_ksi_classifier(cls):
        return "#" + str(cls)
    return cls


def build_query(sem):
    """AGG-фильтр: find (obj) + where (subj).

    Tangle-специфика: если subj использует XNKC*-классификатор, добавляется
    структурный parent-фильтр XNKC0003=Com (родитель — компонент).
    Для custom-классификаторов (ИМЯ) parent-фильтр не добавляется.
    """
    obj = sem["object"]
    subj = sem["subject"]
    if not obj or not subj:
        raise ValueError("Не найдены object или subject в разметке")

    where_branch = {
        "type": "where",
        "key": [prefix_class(subj["class"])],
        "opkey": "equal",
        "opvalue": "equal",
        "val": [subj["value"]],
        "rule": ["notwide"],
        "cmd": None,
    }

    if str(subj["class"]).startswith("XNKC"):
        # Tangle требует parent AGG с XNKC0003=Com
        where_branch = {
            "type": "where",
            "key": ["#XNKC0003"],
            "opkey": "equal",
            "opvalue": "equal",
            "val": ["Com"],
            "rule": ["notwide"],
            "cmd": {
                "type": "get",
                "key": [prefix_class(subj["class"])],
                "opkey": "equal",
                "opvalue": "equal",
                "val": [subj["value"]],
                "rule": ["notwide"],
                "cmd": None,
            },
        }

    return [{
        "type": "combine",
        "opvalue": "equal",
        "and": [
            {
                "type": "find",
                "key": ["Aggregates"],
                "opkey": "equal",
                "opvalue": "equal",
                "cmd": {
                    "type": "get",
                    "key": [prefix_class(obj["class"])],
                    "opkey": "equal",
                    "opvalue": "equal",
                    "val": [obj["value"]],
                    "rule": ["notwide"],
                    "cmd": None,
                },
            },
            where_branch,
        ],
    }]


def build_query_commands(sem):
    """Бинарное дерево для Query."""
    obj = sem["object"]
    subj = sem["subject"]

    # Для XNKC* subj добавляется sub-cmd с get subj
    if str(subj["class"]).startswith("XNKC"):
        where_branch = {
            "Childs": [{
                "Childs": [],
                "ChildCommandsMode": 3,
                "Rules": [12],
                "ValueOperation": 0,
                "KeyOperation": 1,
                "Vals": [subj["value"]],
                "Keys": [prefix_class(subj["class"])],
                "Type": 3,
                "PathResults": None,
                "PathInputs": None,
                "WrongPathResults": None,
            }],
            "ChildCommandsMode": 2,
            "Rules": [12],
            "ValueOperation": 0,
            "KeyOperation": 1,
            "Vals": ["Com"],
            "Keys": ["#XNKC0003"],
            "Type": 0,
            "PathResults": None,
            "PathInputs": None,
            "WrongPathResults": None,
        }
    else:
        where_branch = {
            "Childs": [],
            "ChildCommandsMode": 2,
            "Rules": [12],
            "ValueOperation": 0,
            "KeyOperation": 1,
            "Vals": [subj["value"]],
            "Keys": [prefix_class(subj["class"])],
            "Type": 0,
            "PathResults": None,
            "PathInputs": None,
            "WrongPathResults": None,
        }

    return [{
        "Childs": [
            {
                "Childs": [{
                    "Childs": [],
                    "ChildCommandsMode": 3,
                    "Rules": [12],
                    "ValueOperation": 0,
                    "KeyOperation": 1,
                    "Vals": [obj["value"]],
                    "Keys": [prefix_class(obj["class"])],
                    "Type": 3,
                    "PathResults": None,
                    "PathInputs": None,
                    "WrongPathResults": None,
                }],
                "ChildCommandsMode": 2,
                "Rules": [],
                "ValueOperation": 0,
                "KeyOperation": 1,
                "Vals": [],
                "Keys": ["Aggregates"],
                "Type": 1,
                "PathResults": None,
                "PathInputs": None,
                "WrongPathResults": None,
            },
            where_branch,
        ],
        "ChildCommandsMode": 1,
        "Rules": [],
        "ValueOperation": 0,
        "KeyOperation": 0,
        "Vals": [],
        "Keys": [],
        "Type": 4,
        "PathResults": None,
        "PathInputs": None,
        "WrongPathResults": None,
    }]


def build_parameter_paths(sem):
    """EstimatedParameterPaths для subj.

    Для quantitative: val = числовое значение в стандартных единицах (e.g. "1000")
    Для qualitative:  val = текстовое значение из lvl4 (e.g. "Кирпич")
    """
    opvalue = determine_opvalue(sem["comparative_text"])
    prop_class = sem["property_class"]
    if not prop_class:
        return []

    if sem["flavor"] == "quantitative" and sem["value_std"] is not None:
        val = str(sem["value_std"])
    elif sem["property_value"] is not None:
        val = str(sem["property_value"])
    else:
        val = ""

    query = json.dumps(
        [{
            "type": "get",
            "key": [PARAM_PREFIX + prop_class],
            "opkey": "equal",
            "opvalue": opvalue,
            "val": [val],
            "rule": ["notwide"],
            "cmd": None,
        }],
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return [{
        "Query": query,
        "Factor": 1.0,
        "VariableName": sem["property_name"] or prop_class,
        "Tags": [],
        "Description": None,
        "GetAll": False,
    }]


def build_hashes(sem, query_str):
    obj = sem["object"]
    subj = sem["subject"]
    strict = [
        [{
            "Hash": sha256(normalize({"key": [obj["class"]], "val": [obj["value"]]})),
            "Name": str(obj["class"]).lower(),
            "Value": str(obj["value"]).lower(),
            "Path": None,
        }],
        [{
            "Hash": sha256(normalize({"key": [subj["class"]], "val": [subj["value"]]})),
            "Name": str(subj["class"]).lower(),
            "Value": str(subj["value"]).lower(),
            "Path": None,
        }],
    ]
    query_hashes = [{
        "Hash": sha256(query_str),
        "Name": "[[true,[true]]]",
        "Value": None,
        "Path": None,
    }]
    return strict, query_hashes


def build_extra_properties():
    return {
        "ParameterReferences": "[]",
        "Links": "[]",
        "GroupedTwigs": "[]",
        "IsRequired": False,
        "CheckLinks": "[]",
        "ParametricTwigs": "[]",
        "PriceData": json.dumps({
            "Price": 0.0,
            "Type": None,
            "UnitId": ZERO_GUID,
            "CurrencyCharCode": None,
            "Formula": None,
            "FormulaDescription": None,
        }, ensure_ascii=False),
        "GroupedComputeSelf": False,
    }


def build_package(markup, sem):
    opvalue = determine_opvalue(sem["comparative_text"])
    flavor = sem["flavor"] or "quantitative"
    color = determine_color(flavor, opvalue)

    query = build_query(sem)
    query_str = json.dumps(query, separators=(",", ":"), ensure_ascii=False)

    qc = build_query_commands(sem)
    params = build_parameter_paths(sem)
    strict, qhashes = build_hashes(sem, query_str)
    extra = build_extra_properties()

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    name = markup.get("name") or markup.get("sourceText") or "Untitled"

    return {
        "Name": name,
        "Desc": None,
        "ColorString": color,
        "CompanyId": DEFAULTS["CompanyId"],
        "Type": DEFAULTS["Type"],
        "CodeExp": "(1,1,1)\n.(1,1,1)",
        "IsPrivate": DEFAULTS["IsPrivate"],
        "UserId": DEFAULTS["UserId"],
        "RootTreeId": [],
        "Twigs": [{
            "Twigs": [],
            "FullIndexes": [0],
            "Tags": [],
            "FullCodes": ["1"],
            "Versions": [{
                "CreatedAt": now,
                "Number": 1,
                "Query": query_str,
                "QueryHash": None,
                "StrictHashes": strict,
                "QueryHashes": qhashes,
                "QueryCommands": qc,
                "EstimatedParameterPaths": params,
                "VariableFormulas": [],
                "ExtraProperties": extra,
                "Id": ZERO_GUID,
            }],
            "LogoUrl": None,
            "Desc": None,
            "BindingPatternId": ZERO_GUID,
            "ExternalGuid": ZERO_GUID,
            "ExternalMark": "",
            "ParentId": ZERO_GUID,
            "RootId": ZERO_GUID,
            "Name": name,
            "Code": "1",
            "Owner": "",
            "UpdatedDate": now,
            "LastUpdatesAuthor": "",
            "FullCode": "1",
            "CodePrefix": "",
            "CodeRewrite": None,
            "TwigEntities": 39,
            "ExtraProperties": {},
            "ConcurrencyStamp": "0",
            "Id": ZERO_GUID,
        }],
        "Users": None,
        "Id": ZERO_GUID,
        "ConcurrencyStamp": "0",
        "ExtraProperties": {},
    }


# ===== CLI =====

def main():
    if len(sys.argv) != 3:
        print("Использование: python converter.py <input_markup.json> <output_tangle.json>")
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    markup = parse_markup(in_path)

    sent = markup["sentences"][0]
    sem = determine_semantic(sent)

    package = build_package(markup, sem)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(package, f, ensure_ascii=False, indent=2)

    print("OK: " + in_path + " -> " + out_path)
    print("  Name:   " + package['Name'])
    print("  Color:  " + package['ColorString'] + "  (flavor=" + str(sem['flavor']) + ", opvalue=" + determine_opvalue(sem['comparative_text']) + ")")
    print("  Object: " + sem['object']['class'] + " = " + str(sem['object']['value']) + " (find)")
    print("  Subject:" + sem['subject']['class'] + " = " + str(sem['subject']['value']) + " (where)")
    if sem["property_class"]:
        opvalue = determine_opvalue(sem['comparative_text'])
        if sem['flavor'] == 'quantitative':
            print("  Param:  " + sem['property_class'] + " " + opvalue + " " + str(sem['value_std']) + " " + str(sem['standard_unit']))
        else:
            print("  Param:  " + sem['property_class'] + " " + opvalue + " " + str(sem['property_value']))


if __name__ == "__main__":
    main()
