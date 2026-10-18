import json
import re
from pathlib import Path

import pdfplumber
from pdf2image import convert_from_path


ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "tmp" / "pdfs"
OUTPUT = ROOT / "question-bank.js"
IMAGE_DIR = ROOT / "assets" / "question-images"

YEARS = [110, 111, 112, 113, 114]
SUBJECTS = {
    "0101": ("營建法規與實務", 80),
    "0102": ("建築結構", 40),
    "0103": ("建築構造與施工", 80),
    "0104": ("建築環境控制", 40),
}
OPTION_MARKS = "\ue18c\ue18d\ue18e\ue18f"


def compact(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_headers(text: str) -> str:
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^(代號：|頁次：|等\s*別：|類\s*科：|科\s*目：|考試時間：)", s):
            continue
        if "專門職業及技術人員高等考試" in s:
            continue
        if "暨普通考試不動產經紀人、記帳士考試試題" in s:
            continue
        if re.match(r"^\d+類科技師", s) or "技師考試分階段考試" in s:
            continue
        kept.append(line)
    return "\n".join(kept)


def read_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def find_question_starts(text: str, expected: int):
    starts = []
    cursor = 0
    for number in range(1, expected + 1):
        matches = list(re.finditer(rf"(?m)^{number}\s+", text[cursor:]))
        chosen = None
        for match in matches:
            pos = cursor + match.start()
            next_area = text[pos : pos + 3000]
            if all(mark in next_area for mark in OPTION_MARKS):
                chosen = pos
                break
        if chosen is None:
            raise ValueError(f"question {number} start not found")
        starts.append(chosen)
        cursor = chosen + len(str(number)) + 1
    return starts


def create_question_image(path: Path, question_number: int) -> str:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(path) as pdf:
        page_index = None
        top = None
        bottom = None
        for index, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False)
            starts = [
                word
                for word in words
                if word["text"] == str(question_number) and word["x0"] < 85
            ]
            if not starts:
                continue
            start = starts[0]
            page_index = index
            top = max(0, start["top"] - 10)
            next_starts = [
                word
                for word in words
                if word["text"] == str(question_number + 1)
                and word["x0"] < 85
                and word["top"] > start["top"]
            ]
            bottom = (next_starts[0]["top"] - 8) if next_starts else (page.height - 35)
            page_width = page.width
            page_height = page.height
            break
    if page_index is None:
        raise ValueError(f"{path.name}: cannot locate question {question_number} for image fallback")
    image = convert_from_path(
        path,
        dpi=160,
        first_page=page_index + 1,
        last_page=page_index + 1,
    )[0]
    scale_x = image.width / page_width
    scale_y = image.height / page_height
    crop = image.crop((55 * scale_x, top * scale_y, (page_width - 32) * scale_x, bottom * scale_y))
    filename = f"{path.stem}-{question_number}.png"
    destination = IMAGE_DIR / filename
    crop.save(destination, optimize=True)
    return f"assets/question-images/{filename}"


def parse_questions(path: Path, expected: int):
    text = strip_headers(read_pdf_text(path))
    if expected == 40 and "乙、測驗題部分" in text:
        text = text[text.index("乙、測驗題部分") :]
    starts = find_question_starts(text, expected)
    questions = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        block = text[start:end]
        block = re.sub(rf"^{index + 1}\s+", "", block)
        pieces = re.split(f"([{OPTION_MARKS}])", block)
        if len(pieces) < 9:
            image = create_question_image(path, index + 1)
            questions.append({"q": "請依題圖作答", "options": ["A", "B", "C", "D"], "image": image})
            continue
        prompt = compact(pieces[0])
        options = []
        for mark in OPTION_MARKS:
            try:
                mark_index = pieces.index(mark)
            except ValueError as exc:
                raise ValueError(f"question {index + 1} missing option") from exc
            option_text = pieces[mark_index + 1]
            option_text = re.sub(r"(?m)^(代號：|頁次：).*$", "", option_text)
            options.append(compact(option_text))
        if not prompt or any(not option for option in options):
            image = create_question_image(path, index + 1)
            questions.append({"q": "請依題圖作答", "options": ["A", "B", "C", "D"], "image": image})
        else:
            questions.append({"q": prompt, "options": options})
    return questions


def answer_tokens(path: Path):
    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()
    return [word["text"] for word in words if re.fullmatch(r"[ABCD#]", word["text"])]


def parse_answers(year: int, subject_code: str, expected: int):
    standard = PDF_DIR / f"{year}-{subject_code}-S.pdf"
    corrected = PDF_DIR / f"{year}-{subject_code}-M.pdf"
    selected = standard
    if corrected.exists() and corrected.stat().st_size > 10000:
        corrected_tokens = answer_tokens(corrected)
        if len(corrected_tokens) >= expected:
            selected = corrected
    tokens = answer_tokens(selected)[:expected]
    if len(tokens) != expected:
        raise ValueError(f"{selected.name}: expected {expected} answers, got {len(tokens)}")
    return tokens, selected.name


def source_url(year: int, subject_code: str, kind: str) -> str:
    exam_codes = {110: "110180", 111: "111180", 112: "112190", 113: "113190", 114: "114180"}
    return (
        "https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx"
        f"?t={kind}&code={exam_codes[year]}&c=801&s={subject_code}&q=1"
    )


def main():
    bank = []
    skipped = []
    reports = []
    for year in YEARS:
        for subject_code, (subject, expected) in SUBJECTS.items():
            question_path = PDF_DIR / f"{year}-{subject_code}-Q.pdf"
            print(f"Parsing {year}-{subject_code} {subject}")
            questions = parse_questions(question_path, expected)
            answers, answer_file = parse_answers(year, subject_code, expected)
            kept = 0
            for number, (question, answer) in enumerate(zip(questions, answers), start=1):
                if answer == "#":
                    skipped.append({"year": year, "subject": subject, "questionNo": number})
                    continue
                item = {
                        "id": f"official-{year}-{subject_code}-{number}",
                        "year": year,
                        "subject": subject,
                        "questionNo": number,
                        "q": question["q"],
                        "options": question["options"],
                        "answer": "ABCD".index(answer),
                        "explain": f"考選部公布答案：{answer}",
                        "official": True,
                        "source": source_url(year, subject_code, "Q"),
                        "answerSource": source_url(year, subject_code, "M" if answer_file.endswith("-M.pdf") else "S"),
                    }
                if "image" in question:
                    item["image"] = question["image"]
                bank.append(item)
                kept += 1
            reports.append({"year": year, "subject": subject, "parsed": len(questions), "kept": kept, "answerFile": answer_file})

    payload = "// Generated from official Ministry of Examination PDFs.\n"
    payload += "const officialQuestionBank=" + json.dumps(bank, ensure_ascii=False, separators=(",", ":")) + ";\n"
    payload += "const officialQuestionBankMeta=" + json.dumps({"reports": reports, "skippedCorrected": skipped}, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUTPUT.write_text(payload, encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "questions": len(bank), "skipped": len(skipped), "reports": reports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
