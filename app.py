"""Streamlit UI: конвертер разметки (Модуль семантического анализа) → Тангл.

Запуск локально:
    pip install -r requirements.txt
    streamlit run app.py

Деплой: HuggingFace Spaces (Streamlit SDK).
"""

import json
import sys
import tempfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from converter import (
    parse_markup,
    determine_semantic,
    build_package,
    determine_opvalue,
    determine_color,
)

st.set_page_config(
    page_title="Разметка → Тангл",
    page_icon="🔄",
    layout="centered",
)

st.title("🔄 Конвертер разметки → Тангл")
st.write(
    "Загрузите JSON-файл разметки из **Модуля семантического анализа**, "
    "нажмите кнопку и получите файл проверки для **Тангл** (nanoCAD)."
)

EXAMPLES_DIR = Path(__file__).parent / "examples"

col_up, col_ex = st.columns([3, 1])
with col_up:
    uploaded = st.file_uploader("1. Загрузите JSON разметки", type=["json"])

with col_ex:
    st.write("")
    st.write("")
    use_example = st.button("Попробовать на примере", use_container_width=True)

markup_bytes = None
if uploaded is not None:
    markup_bytes = uploaded.read()
elif use_example:
    sample = EXAMPLES_DIR / "example.json"
    if sample.exists():
        markup_bytes = sample.read_bytes()
    else:
        st.error("Файл примера не найден в папке examples/")

if markup_bytes is not None:
    st.divider()
    if st.button("⚙️ Конвертировать", type="primary", use_container_width=True):
        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp.write(markup_bytes)
                tmp_path = tmp.name

            markup = parse_markup(tmp_path)
            sent = markup["sentences"][0]
            sem = determine_semantic(sent)
            package = build_package(markup, sem)

            opvalue = determine_opvalue(sem["comparative_text"])
            color = package["ColorString"]
            prop = sem["property_class"] or "—"

            st.success("Готово к скачиванию")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Color", color)
            m2.metric("Flavor", sem["flavor"] or "—")
            m3.metric("Op", opvalue)
            m4.metric("Param", prop)

            st.json(
                {
                    "Name": package["Name"],
                    "Object (find)": f'{sem["object"]["class"]} = {sem["object"]["value"]}',
                    "Subject (where)": f'{sem["subject"]["class"]} = {sem["subject"]["value"]}',
                }
            )

            output_json = json.dumps(package, ensure_ascii=False, indent=2)
            st.download_button(
                "📥 Скачать Tangle JSON",
                data=output_json,
                file_name="tangle.json",
                mime="application/json",
                use_container_width=True,
            )

            with st.expander("Показать полный JSON"):
                st.code(output_json, language="json")

        except Exception as e:
            st.error(f"Ошибка конвертации: {e}")
            st.exception(e)

st.divider()
st.caption(
    "Конвертер поддерживает количественные и качественные проверки. "
    "Подробности — в репозитории проекта."
)
