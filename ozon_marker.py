import os
import io
import fitz

CM_TO_PT = 72 / 2.54
FIXED_VERTICAL_LINES_CM = [4.5, 13.5, 17.0]
VERTICAL_LINE_2_OFFSET_PT = 14.7
HORIZONTAL_LINE_TOLERANCE_PT = 2.0
MERGE_LINES_THRESHOLD_PT = 3.0
GRID_STEP_PT = 51.02
CONFLICT_THRESHOLD_PT = 2.0
WAREHOUSE_KEYWORD = "Склад"
WAREHOUSE_LINE_OFFSET_PT = 2.0
PAGE_BOTTOM_MARGIN_PT = 40.0


def cm_to_pt(cm: float) -> float:
    return cm * CM_TO_PT


def extract_horizontal_lines_from_page(page):
    horizontal_lines = []
    drawings = page.get_drawings()
    for drawing in drawings:
        for item in drawing.get("items", []):
            item_type = item[0]
            if item_type == "l":
                p1, p2 = item[1], item[2]
                y0, y1 = p1.y, p2.y
                x0, x1 = min(p1.x, p2.x), max(p1.x, p2.x)
                if abs(y0 - y1) <= HORIZONTAL_LINE_TOLERANCE_PT:
                    y_avg = (y0 + y1) / 2
                    horizontal_lines.append((y_avg, x0, x1))
            elif item_type == "re":
                rect = item[1]
                horizontal_lines.append((rect.y0, rect.x0, rect.x1))
                horizontal_lines.append((rect.y1, rect.x0, rect.x1))
    return horizontal_lines


def merge_close_lines(lines, threshold_pt=MERGE_LINES_THRESHOLD_PT):
    if not lines:
        return []
    sorted_lines = sorted(lines, key=lambda l: l[0])
    merged = []
    for line in sorted_lines:
        if not merged:
            merged.append(line)
        else:
            last_y, last_x0, last_x1 = merged[-1]
            curr_y, curr_x0, curr_x1 = line
            if abs(curr_y - last_y) <= threshold_pt:
                avg_y = (last_y + curr_y) / 2
                min_x = min(last_x0, curr_x0)
                max_x = max(last_x1, last_x1)
                merged[-1] = (avg_y, min_x, max_x)
            else:
                merged.append(line)
    return merged


def is_line_conflicting(y, existing_lines, threshold_pt=CONFLICT_THRESHOLD_PT):
    for line_y, _, _ in existing_lines:
        if abs(y - line_y) <= threshold_pt:
            return True
    return False


def find_warehouse_info(page):
    warehouses = []
    words = page.get_text("words")
    for i, word in enumerate(words):
        word_text = word[4]
        if WAREHOUSE_KEYWORD in word_text:
            x0, y0, x1, y1 = word[:4]
            warehouse_name = ""
            if ": " in word_text:
                parts = word_text.split(": ", 1)
                if len(parts) > 1 and parts[1].strip():
                    warehouse_name = parts[1].strip()
                else:
                    next_words = []
                    for j in range(i + 1, min(i + 5, len(words))):
                        next_word = words[j][4]
                        if abs(words[j][1] - y0) < 5:
                            next_words.append(next_word)
                        else:
                            break
                    warehouse_name = " ".join(next_words).strip()
            else:
                next_words = []
                for j in range(i + 1, min(i + 5, len(words))):
                    next_word = words[j][4]
                    if abs(words[j][1] - y0) < 5:
                        next_words.append(next_word)
                    else:
                        break
                warehouse_name = " ".join(next_words).strip()
            warehouses.append({
                'x0': x0,
                'y0': y0,
                'x1': x1,
                'y1': y1,
                'name': warehouse_name if warehouse_name else "(не указано)"
            })
    return warehouses


def _apply_markings(doc):
    """Внутренняя функция: применяет все линии к документу."""
    line_color = (0, 0, 0)
    line_width_pt = 0.5
    all_warehouses = []
    
    for page_num, page in enumerate(doc):
        page_width = page.rect.width
        page_height = page.rect.height
        shape = page.new_shape()
        
        warehouses = find_warehouse_info(page)
        
        line1_y_start = 0
        if warehouses:
            line1_y_start = warehouses[0]['y1'] + WAREHOUSE_LINE_OFFSET_PT
        
        for idx, x_cm in enumerate(FIXED_VERTICAL_LINES_CM):
            x_pt = cm_to_pt(x_cm)
            if idx == 1:
                x_pt += VERTICAL_LINE_2_OFFSET_PT
            y_start = line1_y_start if idx == 0 else 0
            shape.draw_line(fitz.Point(x_pt, y_start), fitz.Point(x_pt, page_height))
        
        if warehouses:
            for wh in warehouses:
                top_y = wh['y0'] - WAREHOUSE_LINE_OFFSET_PT
                bottom_y = wh['y1'] + WAREHOUSE_LINE_OFFSET_PT
                shape.draw_line(fitz.Point(0, top_y), fitz.Point(page_width, top_y))
                shape.draw_line(fitz.Point(0, bottom_y), fitz.Point(page_width, bottom_y))
                all_warehouses.append({
                    'page': page_num + 1,
                    'name': wh['name']
                })
        
        existing_lines = extract_horizontal_lines_from_page(page)
        merged_lines = merge_close_lines(existing_lines)
        
        if not merged_lines:
            shape.finish(color=line_color, width=line_width_pt)
            shape.commit()
            continue
        
        all_lines_for_conflict = list(merged_lines)
        for wh in warehouses:
            all_lines_for_conflict.append((wh['y0'] - WAREHOUSE_LINE_OFFSET_PT, 0, page_width))
            all_lines_for_conflict.append((wh['y1'] + WAREHOUSE_LINE_OFFSET_PT, 0, page_width))
        
        if page_num == 0:
            min_y = min(line[0] for line in merged_lines)
            max_y_existing = max(line[0] for line in merged_lines)
            max_y = max(max_y_existing, page_height - PAGE_BOTTOM_MARGIN_PT)
            current_y = min_y
            while current_y <= max_y:
                if not is_line_conflicting(current_y, all_lines_for_conflict, CONFLICT_THRESHOLD_PT):
                    shape.draw_line(fitz.Point(0, current_y), fitz.Point(page_width, current_y))
                    all_lines_for_conflict.append((current_y, 0, page_width))
                current_y += GRID_STEP_PT
        else:
            max_y = max(line[0] for line in merged_lines)
            current_y = max_y
            while current_y >= 0:
                if not is_line_conflicting(current_y, all_lines_for_conflict, CONFLICT_THRESHOLD_PT):
                    shape.draw_line(fitz.Point(0, current_y), fitz.Point(page_width, current_y))
                    all_lines_for_conflict.append((current_y, 0, page_width))
                current_y -= GRID_STEP_PT
        
        shape.finish(color=line_color, width=line_width_pt)
        shape.commit()


def mark_ozon_pdf_in_memory(input_pdf: str) -> bytes:
    """
    Размечает PDF линиями и возвращает байты в памяти.
    НЕ создаёт временных файлов на диске.
    
    Args:
        input_pdf: Путь к исходному PDF
    
    Returns:
        bytes: Байты размеченного PDF
    """
    doc = fitz.open(input_pdf)
    _apply_markings(doc)
    
    buffer = io.BytesIO()
    doc.save(buffer, garbage=4, deflate=True)
    doc.close()
    
    marked_bytes = buffer.getvalue()
    buffer.close()
    
    print(f"DEBUG MARKER: Размеченный PDF в памяти ({len(marked_bytes)} байт)")
    return marked_bytes


def mark_ozon_pdf(input_pdf: str, output_pdf: str):
    """
    УСТАРЕЛА: сохраняет размеченный PDF на диск.
    Сохранена для обратной совместимости. Рекомендуется использовать mark_ozon_pdf_in_memory.
    """
    doc = fitz.open(input_pdf)
    _apply_markings(doc)
    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()
    return output_pdf