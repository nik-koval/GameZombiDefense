from __future__ import annotations

import html
import json
import math
import os
import secrets
import sys
from decimal import Decimal
from datetime import date, datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode

try:
    import oracledb
except ImportError:
    oracledb = None


DB_DSN = os.getenv("ZD_DB_DSN", "localhost:1521/XEPDB1")
DB_USER = os.getenv("ZD_DB_USER", "ZD_OWNER")
DB_PASSWORD = os.getenv("ZD_DB_PASSWORD", "")
DB_SCHEMA = os.getenv("ZD_SCHEMA", "").strip()
HOST = os.getenv("ZD_WEB_HOST", "0.0.0.0")
PORT = int(os.getenv("ZD_WEB_PORT", "8000"))

SESSIONS: dict[str, dict[str, Any]] = {}
DB_POOL: Any = None


def repair_mojibake(value: str) -> str:
    candidates = [value]
    for encoding in ("cp1252", "latin1", "cp1251"):
        try:
            candidates.append(value.encode(encoding).decode("utf-8"))
        except UnicodeError:
            pass

    def windows_byte(char: str) -> int | None:
        if ord(char) <= 255:
            return ord(char)
        try:
            encoded = char.encode("cp1252")
        except UnicodeEncodeError:
            return None
        return encoded[0] if len(encoded) == 1 else None

    def repair_windows_segments(text: str) -> str:
        parts: list[str] = []
        segment: list[str] = []

        def flush() -> None:
            if not segment:
                return
            original = "".join(segment)
            if "Ð" in original or "Ñ" in original:
                try:
                    raw = bytes(windows_byte(char) for char in original)
                    parts.append(raw.decode("utf-8"))
                except (TypeError, UnicodeError, ValueError):
                    parts.append(original)
            else:
                parts.append(original)
            segment.clear()

        for char in text:
            if windows_byte(char) is None:
                flush()
                parts.append(char)
            else:
                segment.append(char)
        flush()
        return "".join(parts)

    candidates.append(repair_windows_segments(value))
    try:
        raw = bytearray()
        for char in value:
            try:
                raw.extend(char.encode("latin1"))
            except UnicodeEncodeError:
                raw.extend(char.encode("cp1252"))
        candidates.append(raw.decode("utf-8"))
    except UnicodeError:
        pass

    def score(text: str) -> int:
        cyrillic = sum("\u0400" <= char <= "\u04ff" for char in text)
        bad = sum(text.count(marker) for marker in ("Ð", "Ñ", "Ÿ", "¾", "€", "‚", "ˆ", "Рџ", "Р ", "РЎ", "Рќ", "Рґ", "Рё", "СЊ", "СЃ"))
        return cyrillic - bad * 8

    return max(candidates, key=score)


def esc(value: Any) -> str:
    return html.escape("" if value is None else repair_mojibake(str(value)), quote=True)


def display_value(value: Any) -> Any:
    return repair_mojibake(value) if isinstance(value, str) else value


def qname(name: str) -> str:
    return f"{DB_SCHEMA}.{name}" if DB_SCHEMA else name


def connect() -> Any:
    global DB_POOL
    if oracledb is None:
        raise RuntimeError("Не найден пакет oracledb. Установите зависимости: python -m pip install -r requirements.txt")
    if not DB_PASSWORD:
        raise RuntimeError("Не задан пароль Oracle. Установите переменную окружения ZD_DB_PASSWORD.")
    if DB_POOL is None:
        try:
            DB_POOL = oracledb.create_pool(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN, min=1, max=6, increment=1)
        except AttributeError:
            return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)
    return DB_POOL.acquire()


def rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [column[0].lower() for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]


def row(sql: str, **params: Any) -> dict[str, Any] | None:
    result = rows(sql, **params)
    return result[0] if result else None


def call_game_proc(procedure_name: str, args: list[Any]) -> tuple[bool, str]:
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.callproc("dbms_output.enable", [1_000_000])
            try:
                cursor.callproc(f"{qname('zombie_defense')}.{procedure_name}", args)
                connection.commit()
                return True, read_dbms_output(cursor)
            except Exception as exc:
                connection.rollback()
                output = read_dbms_output(cursor)
                message = friendly_db_error(str(exc).splitlines()[0])
                return False, f"{message}\n{output}".strip()


def read_dbms_output(cursor: Any) -> str:
    lines: list[str] = []
    line_var = cursor.var(str)
    status_var = cursor.var(int)
    while True:
        cursor.callproc("dbms_output.get_line", [line_var, status_var])
        if status_var.getvalue() != 0:
            break
        line = line_var.getvalue()
        if line:
            lines.append(repair_mojibake(line))
    return "\n".join(lines)


def error_notice(ok: bool, message: str) -> str:
    return "" if ok else message


def friendly_db_error(message: str) -> str:
    message = repair_mojibake(message)
    if "ORA-20010" in message:
        return "Имя игрока не может быть пустым."
    if "ORA-20011" in message:
        return "Пароль не может быть пустым."
    if "ORA-20012" in message:
        return "Игрок с таким именем уже существует."
    if "ORA-20013" in message:
        return "Неверный пароль."
    if "ORA-20014" in message:
        return "Игрок не найден."
    if "ORA-20033" in message or "Недостаточно денег для покупки" in message:
        return "Недостаточно средств для покупки башни."
    if "ORA-20041" in message or "Недостаточно денег для улучшения" in message:
        return "Недостаточно средств для улучшения башни."
    if "ORA-20200" in message:
        return "Игрок не найден."
    if "ORA-20201" in message:
        return "Введите имя игрока, на которого применяется эффект."
    if "ORA-20202" in message:
        return "Нельзя выбрать самого себя."
    if "ORA-20203" in message:
        if "гадость" in message.lower():
            return "Сегодня гадость уже отправлена. Следующая гадость будет доступна завтра."
        if "сладость" in message.lower():
            return "Сегодня сладость уже отправлена. Следующая сладость будет доступна завтра."
        return "Сегодня это действие уже использовано. Следующая попытка будет доступна завтра."
    if "ORA-20204" in message:
        return "Выберите сладость или гадость."
    if "ORA-20205" in message:
        return "Игрок с таким именем не найден."
    if "ORA-20206" in message:
        return "Сегодня это действие уже использовано. Следующая попытка будет доступна завтра."
    if ": " in message and message.startswith("ORA-"):
        return message.split(": ", 1)[1]
    return message


def find_player(player_name: str) -> dict[str, Any] | None:
    return row(
        f"""
        SELECT player_id, player_name
        FROM {qname('players')}
        WHERE LOWER(player_name) = LOWER(:player_name)
        """,
        player_name=player_name.strip(),
    )


def active_session(player_id: int) -> dict[str, Any] | None:
    return row(
        f"""
        SELECT session_id, map_id, money, base_health, current_wave, killed_zombies, simulation_time, status
        FROM {qname('game_sessions')}
        WHERE player_id = :player_id
          AND status = 'running'
        ORDER BY session_id DESC
        FETCH FIRST 1 ROW ONLY
        """,
        player_id=player_id,
    )


def latest_session(player_id: int) -> dict[str, Any] | None:
    return row(
        f"""
        SELECT session_id, map_id, money, base_health, current_wave, killed_zombies, simulation_time, status
        FROM {qname('game_sessions')}
        WHERE player_id = :player_id
        ORDER BY session_id DESC
        FETCH FIRST 1 ROW ONLY
        """,
        player_id=player_id,
    )


def session_status(session_id: int, player_id: int) -> dict[str, Any] | None:
    return row(
        f"""
        SELECT
          gs.session_id,
          gs.player_id,
          gs.player_name,
          gs.money,
          gs.base_health,
          gs.current_wave,
          gs.killed_zombies,
          gs.simulation_time,
          gs.status,
          m.map_id,
          m.map_name,
          m.map_width,
          m.map_height,
          (
            SELECT COUNT(*)
            FROM {qname('active_zombies')} az
            JOIN {qname('wave_progress')} wp ON wp.wave_progress_id = az.wave_progress_id
            WHERE wp.session_id = gs.session_id
              AND az.status = 'active'
          ) AS active_zombies
        FROM {qname('game_sessions')} gs
        JOIN {qname('maps')} m ON m.map_id = gs.map_id
        WHERE gs.session_id = :session_id
          AND gs.player_id = :player_id
        """,
        session_id=session_id,
        player_id=player_id,
    )


def maps_with_preview() -> list[dict[str, Any]]:
    maps = rows(
        f"""
        SELECT map_id, map_name, map_width, map_height, start_money, start_base_health, difficulty
        FROM {qname('maps')}
        ORDER BY map_id
        """
    )
    for item in maps:
        item["preview"] = map_preview_html(item)
    return maps


def build_board(map_id: int, session_id: int | None = None) -> list[list[dict[str, Any]]]:
    game_map = row(
        f"SELECT map_width, map_height FROM {qname('maps')} WHERE map_id = :map_id",
        map_id=map_id,
    )
    if not game_map:
        return []

    width = int(game_map["map_width"])
    height = int(game_map["map_height"])
    board = [[{"kind": "empty", "label": "", "title": ""} for _ in range(width)] for _ in range(height)]

    for point in rows(
        f"SELECT point_order, x, y FROM {qname('map_route')} WHERE map_id = :map_id ORDER BY point_order",
        map_id=map_id,
    ):
        board[int(point["y"])][int(point["x"])] = {
            "kind": "route",
            "label": "",
            "title": f"Путь, шаг {point['point_order']}",
        }

    for point in rows(
        f"SELECT build_point_id, x, y FROM {qname('build_points')} WHERE map_id = :map_id",
        map_id=map_id,
    ):
        board[int(point["y"])][int(point["x"])] = {
            "kind": "build",
            "label": "+",
            "title": f"Точка #{point['build_point_id']}",
        }

    if session_id is None:
        return board

    tower_symbol = {"GUN": "G", "SNIPER": "S", "SPLASH": "M"}
    for tower in rows(
        f"""
        SELECT bp.x, bp.y, tt.tower_code, tt.tower_name, tt.level_no
        FROM {qname('placed_towers')} pt
        JOIN {qname('build_points')} bp ON bp.build_point_id = pt.build_point_id
        JOIN {qname('tower_types')} tt ON tt.tower_type_id = pt.tower_type_id
        WHERE pt.session_id = :session_id
        """,
        session_id=session_id,
    ):
        board[int(tower["y"])][int(tower["x"])] = {
            "kind": f"tower tower-{str(tower['tower_code']).lower()}",
            "label": tower_symbol.get(str(tower["tower_code"]), "T"),
            "title": f"{tower['tower_name']} ур. {tower['level_no']}",
        }

    zombie_counts: dict[tuple[int, int], int] = {}
    for zombie in rows(
        f"""
        SELECT mr.x, mr.y
        FROM {qname('active_zombies')} az
        JOIN {qname('wave_progress')} wp ON wp.wave_progress_id = az.wave_progress_id
        JOIN {qname('map_route')} mr ON mr.route_point_id = az.route_point_id
        WHERE wp.session_id = :session_id
          AND az.status = 'active'
        """,
        session_id=session_id,
    ):
        key = (int(zombie["x"]), int(zombie["y"]))
        zombie_counts[key] = zombie_counts.get(key, 0) + 1

    for (x, y), count in zombie_counts.items():
        board[y][x] = {
            "kind": "zombie",
            "label": "Z" if count == 1 else str(min(count, 9)),
            "title": f"Зомби: {count}",
        }

    return board


def shop_rows() -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT tower_type_id, tower_code, tower_name, damage, range_cells, fire_rate, cost, splash_radius
        FROM {qname('tower_types')}
        WHERE level_no = 1
          AND tower_code <> 'SLOW'
        ORDER BY cost, tower_type_id
        """
    )


def build_points(session_id: int) -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT
          bp.build_point_id,
          bp.x,
          bp.y,
          tt.tower_name
        FROM {qname('game_sessions')} gs
        JOIN {qname('build_points')} bp ON bp.map_id = gs.map_id
        LEFT JOIN {qname('placed_towers')} pt
          ON pt.session_id = gs.session_id
         AND pt.build_point_id = bp.build_point_id
        LEFT JOIN {qname('tower_types')} tt ON tt.tower_type_id = pt.tower_type_id
        WHERE gs.session_id = :session_id
        ORDER BY bp.build_point_id
        """,
        session_id=session_id,
    )


def build_point_rows_for_map(map_id: int) -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT build_point_id, x, y
        FROM {qname('build_points')}
        WHERE map_id = :map_id
        ORDER BY build_point_id
        """,
        map_id=map_id,
    )


def placed_towers(session_id: int) -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT
          pt.placed_tower_id,
          bp.x,
          bp.y,
          tt.tower_name,
          tt.level_no,
          tt.damage,
          tt.range_cells,
          tt.fire_rate,
          tt.cost,
          tt.splash_radius,
          ROUND((
            SELECT SUM(spent.cost)
            FROM {qname('tower_types')} spent
            WHERE spent.tower_code = tt.tower_code
              AND spent.level_no <= tt.level_no
          ) * 0.5) AS sell_refund,
          nt.tower_name AS next_name,
          nt.cost AS next_cost,
          nt.damage AS next_damage,
          nt.range_cells AS next_range_cells,
          nt.fire_rate AS next_fire_rate,
          nt.splash_radius AS next_splash_radius
        FROM {qname('placed_towers')} pt
        JOIN {qname('build_points')} bp ON bp.build_point_id = pt.build_point_id
        JOIN {qname('tower_types')} tt ON tt.tower_type_id = pt.tower_type_id
        LEFT JOIN {qname('tower_types')} nt
          ON nt.tower_code = tt.tower_code
         AND nt.level_no = tt.level_no + 1
        WHERE pt.session_id = :session_id
        ORDER BY pt.placed_tower_id
        """,
        session_id=session_id,
    )


def history(player_id: int) -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT gr.result_status, gr.reached_wave, gr.duration, gr.finished_at, m.map_name
        FROM {qname('game_results')} gr
        JOIN {qname('maps')} m ON m.map_id = gr.map_id
        WHERE gr.player_id = :player_id
        ORDER BY gr.finished_at DESC
        FETCH FIRST 10 ROWS ONLY
        """,
        player_id=player_id,
    )


def leaderboard() -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT place_no, map_id, map_name, player_name, result_status, reached_wave, duration, finished_at
        FROM {qname('v_leaderboard')}
        WHERE place_no <= 20
        ORDER BY map_name, place_no
        """
    )


def menu_maps() -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT map_id, map_name
        FROM {qname('maps')}
        ORDER BY map_id
        """
    )


def treat_status(player_id: int) -> dict[str, Any]:
    return row(
        f"""
        SELECT
          CASE WHEN NVL(SUM(CASE WHEN action_type = 'SWEET' THEN 1 ELSE 0 END), 0) = 0 THEN 1 ELSE 0 END AS can_sweet,
          CASE WHEN NVL(SUM(CASE WHEN action_type = 'TRICK' THEN 1 ELSE 0 END), 0) = 0 THEN 1 ELSE 0 END AS can_trick,
          ROUND(GREATEST(0, (TRUNC(SYSDATE) + 1 - SYSDATE) * 86400)) AS cooldown_seconds
        FROM {qname('trick_or_treat_actions')}
        WHERE player_id = :player_id
          AND created_at >= TRUNC(SYSDATE)
        """,
        player_id=player_id,
    ) or {"can_sweet": 1, "can_trick": 1, "cooldown_seconds": 0}


def active_effects(player_id: int) -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT
          pe.effect_type,
          pe.effect_value,
          pe.expires_at,
          pe.is_backfire,
          p.player_name AS source_name
        FROM {qname('player_effects')} pe
        JOIN {qname('players')} p ON p.player_id = pe.source_player_id
        WHERE pe.target_player_id = :player_id
          AND pe.starts_at <= SYSDATE
          AND pe.expires_at > SYSDATE
        ORDER BY pe.expires_at
        """,
        player_id=player_id,
    )


def latest_treat_action(player_id: int, target_player_name: str, action_type: str) -> dict[str, Any] | None:
    return row(
        f"""
        SELECT ta.action_type, ta.backfired
        FROM {qname('trick_or_treat_actions')} ta
        JOIN {qname('players')} p ON p.player_id = ta.target_player_id
        WHERE ta.player_id = :player_id
          AND LOWER(p.player_name) = LOWER(:target_player_name)
          AND ta.action_type = :action_type
          AND ta.created_at >= TRUNC(SYSDATE)
        ORDER BY ta.created_at DESC
        FETCH FIRST 1 ROW ONLY
        """,
        player_id=player_id,
        target_player_name=target_player_name.strip(),
        action_type=action_type.strip().upper(),
    )


def treat_success_notice(target_player_name: str, action_type: str, backfired: bool) -> str:
    target = target_player_name.strip()
    action = action_type.strip().upper()
    if action == "SWEET":
        return f'Сладость отправлена игроку "{target}": +100 стартовых монет на 12 часов.'
    if backfired:
        return f'Гадость отправлена игроку "{target}": зомби быстрее на 15% на 12 часов. Эффект сработал и на тебе.'
    return f'Гадость отправлена игроку "{target}": зомби быстрее на 15% на 12 часов.'


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        return repair_mojibake(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return json_value(value)


def add_zombie_positions(zombies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for zombie in zombies:
        progress = float(zombie["segment_progress"])
        x1 = float(zombie["route_x"])
        y1 = float(zombie["route_y"])
        x2 = float(zombie["next_x"])
        y2 = float(zombie["next_y"])
        zombie["x"] = x1 + (x2 - x1) * progress
        zombie["y"] = y1 + (y2 - y1) * progress
        zombie["hp_percent"] = max(0, min(100, float(zombie["current_health"]) / float(zombie["base_health"]) * 100))
    return zombies


def active_zombie_rows(session_id: int) -> list[dict[str, Any]]:
    zombies = rows(
        f"""
        SELECT
          az.active_zombie_id,
          az.zombie_type_id,
          az.current_health,
          NVL(NULLIF(az.max_health, 0), zt.base_health) AS base_health,
          NVL(NULLIF(az.armor_multiplier, 0), 1) AS armor_multiplier,
          az.segment_progress,
          zt.base_speed,
          zt.zombie_type_name,
          mr.x AS route_x,
          mr.y AS route_y,
          mr.point_order AS route_order,
          NVL(nr.x, mr.x) AS next_x,
          NVL(nr.y, mr.y) AS next_y
        FROM {qname('active_zombies')} az
        JOIN {qname('zombie_types')} zt ON zt.zombie_type_id = az.zombie_type_id
        JOIN {qname('wave_progress')} wp ON wp.wave_progress_id = az.wave_progress_id
        JOIN {qname('map_route')} mr ON mr.route_point_id = az.route_point_id
        LEFT JOIN {qname('map_route')} nr
          ON nr.map_id = mr.map_id
         AND nr.point_order = mr.point_order + 1
        WHERE wp.session_id = :session_id
          AND az.status = 'active'
        ORDER BY az.active_zombie_id
        """,
        session_id=session_id,
    )
    return add_zombie_positions(zombies)


def zombie_rows_by_ids(session_id: int, zombies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zombie_ids = list(dict.fromkeys(int(zombie["active_zombie_id"]) for zombie in zombies))
    if not zombie_ids:
        return []
    params: dict[str, Any] = {"session_id": session_id}
    placeholders: list[str] = []
    for index, zombie_id in enumerate(zombie_ids):
        key = f"id{index}"
        params[key] = zombie_id
        placeholders.append(f":{key}")
    result = rows(
        f"""
        SELECT
          az.active_zombie_id,
          az.zombie_type_id,
          az.current_health,
          NVL(NULLIF(az.max_health, 0), zt.base_health) AS base_health,
          NVL(NULLIF(az.armor_multiplier, 0), 1) AS armor_multiplier,
          az.segment_progress,
          az.status,
          zt.base_speed,
          zt.zombie_type_name,
          zt.reward,
          mr.x AS route_x,
          mr.y AS route_y,
          mr.point_order AS route_order,
          NVL(nr.x, mr.x) AS next_x,
          NVL(nr.y, mr.y) AS next_y
        FROM {qname('active_zombies')} az
        JOIN {qname('zombie_types')} zt ON zt.zombie_type_id = az.zombie_type_id
        JOIN {qname('wave_progress')} wp ON wp.wave_progress_id = az.wave_progress_id
        JOIN {qname('map_route')} mr ON mr.route_point_id = az.route_point_id
        LEFT JOIN {qname('map_route')} nr
          ON nr.map_id = mr.map_id
         AND nr.point_order = mr.point_order + 1
        WHERE wp.session_id = :session_id
          AND az.active_zombie_id IN ({", ".join(placeholders)})
        ORDER BY az.active_zombie_id
        """,
        **params,
    )
    return add_zombie_positions(result)


def tower_rows(session_id: int) -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT
          pt.placed_tower_id,
          pt.remaining_cooldown,
          bp.build_point_id,
          bp.x,
          bp.y,
          tt.tower_type_id,
          tt.tower_code,
          tt.tower_name,
          tt.level_no,
          tt.range_cells,
          tt.damage,
          tt.fire_rate,
          tt.cost,
          tt.splash_radius,
          0 AS slow_percent,
          0 AS slow_duration
        FROM {qname('placed_towers')} pt
        JOIN {qname('build_points')} bp ON bp.build_point_id = pt.build_point_id
        JOIN {qname('tower_types')} tt ON tt.tower_type_id = pt.tower_type_id
        WHERE pt.session_id = :session_id
        ORDER BY pt.placed_tower_id
        """,
        session_id=session_id,
    )


def route_rows(map_id: int) -> list[dict[str, Any]]:
    return rows(
        f"SELECT point_order, x, y FROM {qname('map_route')} WHERE map_id = :map_id ORDER BY point_order",
        map_id=map_id,
    )


def zombie_type_rows() -> list[dict[str, Any]]:
    return rows(
        f"""
        SELECT zombie_type_id, zombie_type_name, base_health, base_speed, armor, reward
        FROM {qname('zombie_types')}
        ORDER BY zombie_type_id
        """
    )


def map_decorations(map_id: int) -> list[dict[str, Any]]:
    decorations = {
        1: [
            {"x": 10.9, "y": 1.1, "w": 1.2, "h": 0.6, "kind": "warehouse", "title": "Серый склад"},
            {"x": 0.35, "y": 1.35, "w": 0.65, "h": 0.4, "kind": "car", "variant": "blue", "title": "Машина"},
            {"x": 0.35, "y": 4.8, "w": 0.65, "h": 0.4, "kind": "car", "variant": "red", "title": "Машина"},
            {"x": 11.35, "y": 1.35, "w": 0.65, "h": 0.4, "kind": "car", "variant": "yellow", "title": "Погрузчик"},
            {"x": 11.35, "y": 4.8, "w": 0.65, "h": 0.4, "kind": "car", "variant": "dark", "title": "Фургон"},
        ],
        2: [
            {"x": 0.8, "y": 7.3, "w": 1.5, "h": 1.4, "kind": "tree", "title": "Дерево"},
            {"x": 1.7, "y": 7.7, "w": 1.2, "h": 0.8, "kind": "bush", "title": "Куст"},
            {"x": 12.4, "y": 7.3, "w": 1.5, "h": 1.4, "kind": "tree", "title": "Дерево"},
            {"x": 11.0, "y": 7.8, "w": 1.5, "h": 0.9, "kind": "bush", "title": "Куст"},
        ],
        3: [
            {"x": 1.2, "y": 0.7, "w": 3.0, "h": 1.4, "kind": "lab", "title": "Лабораторный блок"},
            {"x": 13.9, "y": 8.4, "w": 1.3, "h": 1.3, "kind": "tank", "title": "Биокапсула"},
            {"x": 11.4, "y": 7.5, "w": 1.4, "h": 0.9, "kind": "console", "title": "Пульт управления"},
            {"x": 0.8, "y": 4.1, "w": 1.0, "h": 0.9, "kind": "crate", "title": "Ящики оборудования"},
            {"x": 14.3, "y": 4.8, "w": 1.0, "h": 1.0, "kind": "tank", "title": "Резервуар"},
        ],
    }
    build_points_for_map = build_point_rows_for_map(map_id)
    route = route_rows(map_id)
    return [
        item
        for item in decorations.get(map_id, [])
        if not decoration_is_blocked(item, build_points_for_map, route)
    ]


def decoration_rect(decoration: dict[str, Any], margin: float = 0) -> tuple[float, float, float, float]:
    x = float(decoration["x"])
    y = float(decoration["y"])
    width = float(decoration.get("w", 1))
    height = float(decoration.get("h", 1))
    return (
        x - width / 2 - margin,
        x + width / 2 + margin,
        y - height / 2 - margin,
        y + height / 2 + margin,
    )


def decoration_is_blocked(decoration: dict[str, Any], points: list[dict[str, Any]], route: list[dict[str, Any]]) -> bool:
    return decoration_overlaps_points(decoration, points, clearance=0.7) or decoration_overlaps_route(decoration, route, clearance=0.75)


def decoration_overlaps_points(decoration: dict[str, Any], points: list[dict[str, Any]], clearance: float = 0) -> bool:
    left, right, top, bottom = decoration_rect(decoration, clearance)
    for point in points:
        px = float(point["x"])
        py = float(point["y"])
        if left <= px <= right and top <= py <= bottom:
            return True
    return False


def decoration_overlaps_route(decoration: dict[str, Any], route: list[dict[str, Any]], clearance: float = 0) -> bool:
    left, right, top, bottom = decoration_rect(decoration, clearance)
    ordered = sorted(route, key=lambda item: int(item["point_order"]))
    for point in ordered:
        px = float(point["x"])
        py = float(point["y"])
        if left <= px <= right and top <= py <= bottom:
            return True
    for first, second in zip(ordered, ordered[1:]):
        x1 = float(first["x"])
        y1 = float(first["y"])
        x2 = float(second["x"])
        y2 = float(second["y"])
        if min(x1, x2) <= right and max(x1, x2) >= left and min(y1, y2) <= bottom and max(y1, y2) >= top:
            return True
    return False


def state_payload(
    current_user: dict[str, Any],
    shots: list[dict[str, Any]] | None = None,
    rewards: list[dict[str, Any]] | None = None,
    notice: str = "",
) -> dict[str, Any]:
    player_id = int(current_user["player_id"])
    session = active_session(player_id) or latest_session(player_id)
    if not session:
        return {"hasSession": False, "player": current_user, "notice": notice, "shots": shots or [], "rewards": rewards or []}
    status = session_status(int(session["session_id"]), player_id)
    if not status:
        return {"hasSession": False, "player": current_user, "notice": "Сессия не найдена.", "shots": [], "rewards": []}
    map_id = int(status["map_id"])
    session_id = int(status["session_id"])
    return {
        "hasSession": True,
        "player": current_user,
        "notice": notice,
        "session": status,
        "route": route_rows(map_id),
        "decorations": map_decorations(map_id),
        "buildPoints": build_points(session_id),
        "shop": shop_rows(),
        "towers": tower_rows(session_id),
        "upgrades": placed_towers(session_id),
        "zombieTypes": zombie_type_rows(),
        "zombies": active_zombie_rows(session_id),
        "history": history(player_id),
        "shots": shots or [],
        "rewards": rewards or [],
    }


def infer_shots(
    before_zombies: list[dict[str, Any]],
    after_zombies: list[dict[str, Any]],
    towers: list[dict[str, Any]],
    delta_time: float = 0,
) -> list[dict[str, Any]]:
    before_by_id = {int(item["active_zombie_id"]): item for item in before_zombies}
    after_by_id = {int(item["active_zombie_id"]): item for item in after_zombies}
    result: list[dict[str, Any]] = []
    hit_samples: list[tuple[int, dict[str, Any], float]] = []
    for before in before_zombies:
        zombie_id = int(before["active_zombie_id"])
        after = after_by_id.get(zombie_id)
        before_hp = float(before["current_health"])
        after_hp = float(after["current_health"]) if after else 0
        damage_delta = before_hp - after_hp
        if damage_delta <= 0:
            continue
        target = after or before
        hit_samples.append((zombie_id, target, damage_delta))
    for after in after_zombies:
        zombie_id = int(after["active_zombie_id"])
        if zombie_id in before_by_id:
            continue
        damage_delta = float(after.get("base_health", after["current_health"])) - float(after["current_health"])
        if damage_delta > 0:
            hit_samples.append((zombie_id, after, damage_delta))
    ready_threshold = max(0, float(delta_time)) + 0.0001
    used_towers: set[int] = set()
    for zombie_id, target, damage_delta in hit_samples:
        if not towers:
            continue
        target_x = float(target["x"])
        target_y = float(target["y"])
        candidates: list[tuple[float, dict[str, Any]]] = []
        for tower in towers:
            tower_id = int(tower["placed_tower_id"])
            if tower_id in used_towers:
                continue
            distance = math.hypot(float(tower["x"]) - target_x, float(tower["y"]) - target_y)
            cooldown = float(tower.get("remaining_cooldown", 0) or 0)
            if cooldown <= ready_threshold and distance <= float(tower["range_cells"]):
                candidates.append((distance, tower))
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0])
        remaining_damage = damage_delta
        for _, tower in candidates:
            tower_id = int(tower["placed_tower_id"])
            used_towers.add(tower_id)
            result.append(
                {
                    "towerId": tower_id,
                    "fromX": float(tower["x"]),
                    "fromY": float(tower["y"]),
                    "toX": target_x,
                    "toY": target_y,
                    "zombieId": zombie_id,
                }
            )
            remaining_damage -= max(1, float(tower.get("damage", 1) or 1))
            if remaining_damage <= 0:
                break
    return result[:16]


def infer_rewards(after_zombies: list[dict[str, Any]], expected_count: int, earned_money: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    remaining_money = max(0, earned_money)
    remaining_count = max(0, expected_count)
    if remaining_count == 0 or remaining_money == 0:
        return result
    for zombie in after_zombies:
        if str(zombie.get("status", "")).lower() != "inactive" or float(zombie["current_health"]) > 0:
            continue
        reward = int(zombie["reward"])
        if reward > remaining_money:
            continue
        result.append(
            {
                "x": float(zombie["x"]),
                "y": float(zombie["y"]),
                "reward": reward,
                "zombieId": int(zombie["active_zombie_id"]),
            }
        )
        remaining_count -= 1
        remaining_money -= reward
        if remaining_count == 0 or remaining_money <= 0:
            break
    return result[:10]


def layout(title: str, body: str, current_user: dict[str, Any] | None, notice: str = "") -> bytes:
    user_html = ""
    if current_user:
        user_html = f"""
          <span>{esc(current_user["player_name"])}</span>
          <a class="ghost" href="/logout">Выйти</a>
        """
    notice_html = f'<div class="notice error">{esc(notice)}</div>' if notice else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101114;
      --panel: #191b20;
      --panel-2: #21242b;
      --line: #343945;
      --text: #f3f4f6;
      --muted: #a5adbb;
      --accent: #ffcf5a;
      --accent-2: #50d890;
      --danger: #ff6b6b;
      --route: #5b6474;
      --build: #2f7cc0;
      --zombie: #69d66f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at 20% 10%, #29313d 0, transparent 28rem), var(--bg);
      color: var(--text);
      font: 15px/1.45 "Segoe UI", Arial, sans-serif;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(16, 17, 20, .86);
      position: sticky;
      top: 0;
      z-index: 4;
      backdrop-filter: blur(10px);
    }}
    .brand {{ font-size: 19px; font-weight: 800; letter-spacing: 0; }}
    .userbox {{ display: flex; align-items: center; gap: 10px; color: var(--muted); }}
    main {{ width: min(1220px, 100%); margin: 0 auto; padding: 24px; }}
    h1 {{ margin: 0 0 14px; font-size: clamp(28px, 4vw, 48px); line-height: 1.05; letter-spacing: 0; }}
    h2 {{ margin: 0 0 12px; font-size: 20px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 10px; font-size: 16px; color: var(--muted); letter-spacing: 0; }}
    .grid {{ display: grid; gap: 16px; }}
    .game-layout {{ grid-template-columns: minmax(420px, 1fr) 360px; align-items: start; }}
    .panel {{
      background: rgba(25, 27, 32, .92);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .notice {{
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 5000;
      width: min(380px, calc(100vw - 36px));
      white-space: pre-line;
      margin: 0;
      padding: 12px 14px;
      border: 1px solid #5b5131;
      background: #2a2517;
      border-radius: 8px;
      color: #ffe6a3;
      box-shadow: 0 12px 32px rgba(0, 0, 0, .42);
      pointer-events: none;
      animation: noticePop .18s ease-out;
    }}
    .notice.error {{ border-color: #814040; background: #2a1717; color: #ffc9c9; }}
    .game-notice[hidden] {{ display: none; }}
    .auth {{ width: min(920px, 100%); display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    form {{ margin: 0; }}
    label {{ display: block; color: var(--muted); font-size: 13px; margin: 10px 0 6px; }}
    input, select {{
      width: 100%;
      min-height: 40px;
      background: #101216;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }}
    button, .button, .ghost {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid #776025;
      background: var(--accent);
      color: #17140a;
      font-weight: 700;
      cursor: pointer;
    }}
    .ghost {{ background: transparent; color: var(--text); border-color: var(--line); }}
    .danger {{ background: var(--danger); border-color: #9e3535; color: #210707; }}
    button:disabled {{
      cursor: not-allowed;
      opacity: .56;
      background: var(--panel-2);
      color: var(--muted);
      border-color: var(--line);
    }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .stats {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; margin-bottom: 16px; }}
    .stat {{ padding: 10px; border: 1px solid var(--line); background: var(--panel-2); border-radius: 8px; }}
    .stat b {{ display: block; font-size: 20px; }}
    .stat span {{ color: var(--muted); font-size: 12px; }}
    .board-wrap {{ overflow: auto; padding-bottom: 4px; }}
    .playfield {{
      display: block;
      position: relative;
      width: max-content;
      min-width: 0;
      padding: 18px;
      border-radius: 8px;
      overflow: hidden;
      background:
        radial-gradient(circle at 18% 18%, rgba(78, 131, 41, .9) 0 16px, transparent 17px),
        radial-gradient(circle at 82% 22%, rgba(55, 114, 46, .9) 0 20px, transparent 21px),
        radial-gradient(circle at 70% 78%, rgba(93, 151, 45, .75) 0 18px, transparent 19px),
        linear-gradient(135deg, #79a943, #4f8d35 42%, #6fa33c);
      box-shadow: inset 0 0 0 2px rgba(18, 42, 22, .7), inset 0 0 50px rgba(23, 54, 22, .62);
    }}
    .playfield.map-warehouse {{
      background:
        linear-gradient(90deg, rgba(255,255,255,.14) 0 2px, transparent 2px 74px),
        linear-gradient(0deg, rgba(255,255,255,.08) 0 1px, transparent 1px 42px),
        radial-gradient(circle at 10% 18%, rgba(98, 103, 111, .55) 0 28px, transparent 29px),
        linear-gradient(135deg, #5d6269, #444950 52%, #6a6f75);
      box-shadow: inset 0 0 0 2px rgba(33, 36, 40, .8), inset 0 0 60px rgba(12, 14, 16, .42);
    }}
    .playfield.map-warehouse .road-border {{ stroke: #34383d; }}
    .playfield.map-warehouse .road-main {{ stroke: #8b9198; }}
    .playfield.map-warehouse .road-highlight {{ stroke: rgba(255, 217, 88, .72); stroke-width: 3; stroke-dasharray: 20 24; }}
    .playfield.map-lab {{
      background:
        linear-gradient(90deg, rgba(116, 219, 230, .08) 0 2px, transparent 2px 76px),
        linear-gradient(0deg, rgba(255,255,255,.07) 0 1px, transparent 1px 38px),
        radial-gradient(circle at 12% 12%, rgba(92, 169, 176, .24) 0 34px, transparent 35px),
        linear-gradient(135deg, #283641, #1e2832 50%, #344654);
      box-shadow: inset 0 0 0 2px rgba(103, 154, 169, .45), inset 0 0 64px rgba(0, 0, 0, .35);
    }}
    .playfield.map-lab .road-border {{ stroke: #1a222b; }}
    .playfield.map-lab .road-main {{ stroke: #99aab5; }}
    .playfield.map-lab .road-highlight {{ stroke: rgba(119, 234, 240, .64); stroke-width: 3; stroke-dasharray: 10 16; }}
    .board {{
      display: grid;
      gap: 4px;
      width: max-content;
      min-width: 0;
      position: relative;
      z-index: 3;
    }}
    .cell {{
      width: 34px;
      aspect-ratio: 1;
      border-radius: 12px;
      display: grid;
      place-items: center;
      background: transparent;
      border: 1px solid transparent;
      font-weight: 900;
      color: var(--muted);
      background-position: center;
      background-repeat: no-repeat;
      background-size: 82%;
      position: relative;
      overflow: visible;
    }}
    .route {{ background: transparent; border-color: transparent; box-shadow: none; }}
    .build {{
      background:
        radial-gradient(circle, rgba(180, 205, 224, .9) 0 38%, rgba(87, 112, 125, .95) 40% 58%, rgba(48, 59, 64, .9) 60%);
      border-color: #b4cde0;
      color: transparent;
      box-shadow: 0 3px 7px rgba(0,0,0,.28);
    }}
    .tower {{ background: transparent; border-color: transparent; color: transparent; text-shadow: none; }}
    .tower::before {{
      content: "";
      position: absolute;
      left: 5px;
      top: 10px;
      width: 24px;
      height: 19px;
      border-radius: 50% 50% 42% 42%;
      background:
        radial-gradient(circle at 50% 42%, rgba(255,255,255,.32) 0 3px, transparent 4px),
        linear-gradient(#a89259, #5a4d34);
      border: 2px solid #302a20;
      box-shadow: 0 3px 0 rgba(0,0,0,.25);
    }}
    .tower::after {{
      content: "";
      position: absolute;
      left: 16px;
      top: 9px;
      width: 18px;
      height: 7px;
      border-radius: 5px;
      background: linear-gradient(#f1d27a, #8a6427);
      border: 1px solid #342515;
      transform: rotate(-20deg);
      transform-origin: 3px 50%;
      box-shadow: 0 1px 0 rgba(0,0,0,.28);
    }}
    .tower-sniper::before {{ background: linear-gradient(#9d8fe2, #4f427d); border-color: #2d254d; }}
    .tower-sniper::after {{ width: 23px; height: 4px; top: 13px; left: 15px; background: linear-gradient(#eee9ff, #8773d2); transform: rotate(-28deg); }}
    .tower-splash::before {{ background: linear-gradient(#d99a4e, #684326); border-color: #3b2718; }}
    .tower-splash::after {{
      left: 13px;
      top: 6px;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      background: radial-gradient(circle at 35% 30%, #ffe0a0, #b75d25 70%);
      transform: none;
    }}
    .zombie {{ background: #123a20; border-color: var(--zombie); color: #bcffc2; }}
    .cell.active-build {{ outline: 2px solid var(--accent); outline-offset: 2px; cursor: pointer; }}
    .cell.clickable {{ cursor: pointer; }}
    .zombie-piece {{
      position: absolute;
      left: 0;
      top: 0;
      width: 34px;
      height: 42px;
      --zombie-x: 0px;
      --zombie-y: 0px;
      transform: translate3d(calc(var(--zombie-x) - 50%), calc(var(--zombie-y) - 100%), 0);
      color: transparent;
      font-weight: 900;
      pointer-events: none;
      transition: none;
      will-change: transform;
      z-index: 4;
      filter: drop-shadow(0 4px 2px rgba(0,0,0,.36));
    }}
    .zombie-sprite {{
      position: absolute;
      left: 50%;
      bottom: 0;
      width: 28px;
      height: 38px;
      transform: translateX(-50%);
      --skin: #76b95d;
      --skin-dark: #346c33;
      --cloth: #4f6541;
      --accent-z: #cde88d;
    }}
    .zombie-sprite .head {{
      position: absolute;
      left: 5px;
      top: 0;
      width: 16px;
      height: 15px;
      border-radius: 48% 52% 46% 54%;
      background:
        radial-gradient(circle at 35% 36%, #111 0 1.8px, transparent 2px),
        radial-gradient(circle at 64% 36%, #111 0 1.8px, transparent 2px),
        linear-gradient(var(--skin), var(--skin-dark));
      border: 1px solid #1f3b22;
      z-index: 3;
    }}
    .zombie-sprite .body {{
      position: absolute;
      left: 5px;
      top: 13px;
      width: 18px;
      height: 17px;
      border-radius: 8px 8px 7px 7px;
      background: linear-gradient(var(--accent-z), var(--cloth));
      border: 1px solid rgba(18, 36, 20, .9);
      z-index: 2;
    }}
    .zombie-sprite .arm {{
      position: absolute;
      top: 16px;
      width: 7px;
      height: 17px;
      border-radius: 8px;
      background: linear-gradient(var(--skin), var(--skin-dark));
      border: 1px solid rgba(18, 36, 20, .75);
      transform-origin: 50% 0;
      z-index: 1;
    }}
    .zombie-sprite .arm.left {{ left: 2px; transform: rotate(12deg); }}
    .zombie-sprite .arm.right {{ right: 2px; transform: rotate(-12deg); }}
    .zombie-sprite .leg {{
      position: absolute;
      top: 29px;
      width: 7px;
      height: 11px;
      border-radius: 3px 3px 5px 5px;
      background: linear-gradient(#425238, #222c22);
      z-index: 1;
    }}
    .zombie-sprite .leg.left {{ left: 7px; transform: rotate(5deg); }}
    .zombie-sprite .leg.right {{ right: 7px; transform: rotate(-5deg); }}
    .zombie-piece.zombie-fast .zombie-sprite {{
      width: 22px;
      height: 34px;
      --skin: #b8ec5e;
      --skin-dark: #5f9c34;
      --cloth: #355b2c;
      --accent-z: #f2f37a;
    }}
    .zombie-piece.zombie-fast .zombie-sprite .head {{ left: 4px; width: 14px; height: 13px; }}
    .zombie-piece.zombie-fast .zombie-sprite .body {{
      left: 4px;
      top: 12px;
      width: 15px;
      height: 16px;
      background:
        linear-gradient(135deg, transparent 0 34%, rgba(255,255,160,.9) 35% 45%, transparent 46% 100%),
        linear-gradient(var(--accent-z), var(--cloth));
    }}
    .zombie-piece.zombie-fast .zombie-sprite .arm {{ top: 15px; height: 15px; width: 6px; }}
    .zombie-piece.zombie-fast .zombie-sprite .arm.left {{ left: 1px; transform: rotate(24deg); }}
    .zombie-piece.zombie-fast .zombie-sprite .arm.right {{ right: 1px; transform: rotate(-24deg); }}
    .zombie-piece.zombie-fast .zombie-sprite .leg {{ top: 27px; height: 10px; width: 6px; }}
    .zombie-piece.zombie-fast .zombie-sprite .leg.left {{ left: 5px; transform: rotate(-14deg); }}
    .zombie-piece.zombie-fast .zombie-sprite .leg.right {{ right: 5px; transform: rotate(14deg); }}
    .zombie-piece.zombie-tank {{
      width: 44px;
      height: 50px;
    }}
    .zombie-piece.zombie-tank .zombie-sprite {{
      width: 36px;
      height: 44px;
      --skin: #55734a;
      --skin-dark: #243a27;
      --cloth: #4c4735;
      --accent-z: #8a7d45;
    }}
    .zombie-piece.zombie-tank .zombie-sprite .head {{ left: 8px; width: 20px; height: 18px; border-width: 2px; }}
    .zombie-piece.zombie-tank .zombie-sprite .body {{ left: 6px; top: 16px; width: 24px; height: 21px; border-width: 2px; }}
    .zombie-piece.zombie-tank .zombie-sprite .arm {{ top: 21px; width: 9px; height: 20px; }}
    .zombie-piece.zombie-tank .zombie-sprite .leg {{ top: 34px; width: 9px; height: 12px; }}
    .hpbar {{
      position: absolute;
      left: 50%;
      top: -8px;
      width: 34px;
      height: 5px;
      transform: translateX(-50%);
      border-radius: 999px;
      background: #3b1616;
      overflow: hidden;
      border: 1px solid #2a0c0c;
    }}
    .hpbar span {{ display: block; height: 100%; background: linear-gradient(90deg, #ff6b6b, #ffe66d, #69d66f); }}
    .shot-layer {{ position: absolute; inset: 0; pointer-events: none; z-index: 5; }}
    .shot {{
      position: absolute;
      height: 4px;
      background: linear-gradient(90deg, #fff7b0, #ffae2e);
      box-shadow: 0 0 18px #ffe66d, 0 0 4px #fff;
      transform-origin: left center;
      animation: shotFlash .24s ease-out forwards;
    }}
    .reward-layer {{ position: absolute; inset: 0; pointer-events: none; z-index: 6; }}
    .reward-pop {{
      position: absolute;
      min-width: 34px;
      transform: translate(-50%, -50%);
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(18, 27, 18, .86);
      border: 1px solid rgba(255, 215, 91, .8);
      color: #ffe886;
      font-weight: 900;
      text-align: center;
      text-shadow: 0 1px 0 #000;
      animation: rewardRise 1.05s ease-out forwards;
    }}
    .scenery {{ position: absolute; pointer-events: none; z-index: 0; }}
    .forest-band {{
      inset: -20px;
      background:
        radial-gradient(circle at 5% 15%, #2f7e27 0 20px, transparent 21px),
        radial-gradient(circle at 9% 28%, #226f2a 0 22px, transparent 23px),
        radial-gradient(circle at 95% 20%, #2f7e27 0 21px, transparent 22px),
        radial-gradient(circle at 92% 35%, #226f2a 0 24px, transparent 25px),
        radial-gradient(circle at 82% 96%, #2f7e27 0 20px, transparent 21px);
      opacity: .88;
    }}
    .river {{
      left: 45%;
      top: -10%;
      width: 90px;
      height: 130%;
      transform: rotate(26deg);
      border-radius: 999px;
      background: linear-gradient(90deg, #5eb4d6, #92d7ef 45%, #3b93bd);
      box-shadow: inset 0 0 20px rgba(255,255,255,.38), 0 0 0 5px rgba(59, 104, 73, .35);
      opacity: .9;
    }}
    .pond {{
      right: 4%;
      top: 8%;
      width: 120px;
      height: 72px;
      border-radius: 45% 55% 52% 48%;
      background: radial-gradient(circle at 35% 30%, #a6e8f7, #4ea4cb 72%);
      box-shadow: 0 0 0 5px rgba(47, 103, 65, .45);
      opacity: .82;
    }}
    .road-layer {{ position: absolute; inset: 0; z-index: 1; pointer-events: none; }}
    .road-border {{ fill: none; stroke: rgba(105, 76, 36, .9); stroke-width: 18; stroke-linecap: round; stroke-linejoin: round; }}
    .road-main {{ fill: none; stroke: #d7b46c; stroke-width: 11; stroke-linecap: round; stroke-linejoin: round; }}
    .road-highlight {{ fill: none; stroke: rgba(255,255,255,.22); stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 14 22; }}
    .build-clear-layer {{ position: absolute; inset: 0; z-index: 2; pointer-events: none; }}
    .build-clear {{
      position: absolute;
      width: 42px;
      height: 42px;
      transform: translate(-50%, -50%);
      border-radius: 50%;
      opacity: .64;
      background:
        radial-gradient(circle at 35% 30%, rgba(125, 171, 73, .78) 0 30%, rgba(82, 145, 52, .62) 62%, transparent 66%);
    }}
    .playfield.map-warehouse .build-clear {{
      background:
        radial-gradient(circle at 35% 30%, rgba(110, 116, 122, .76) 0 30%, rgba(76, 82, 88, .58) 62%, transparent 66%);
    }}
    .playfield.map-lab .build-clear {{
      background:
        radial-gradient(circle at 35% 30%, rgba(91, 151, 165, .72) 0 30%, rgba(55, 89, 105, .56) 62%, transparent 66%);
    }}
    .decor-layer {{ position: absolute; inset: 0; z-index: 2; pointer-events: none; }}
    .decor-object {{ position: absolute; transform: translate(-50%, -50%); filter: drop-shadow(0 7px 5px rgba(0,0,0,.34)); }}
    .decor-object > div {{ position: absolute; inset: 0; }}
    .range-layer {{ position: absolute; inset: 0; z-index: 4; pointer-events: none; }}
    .range-circle {{
      position: absolute;
      transform: translate(-50%, -50%);
      border-radius: 50%;
      border: 2px dashed rgba(255, 220, 90, .94);
      background: rgba(255, 226, 95, .08);
      box-shadow: inset 0 0 24px rgba(255, 220, 90, .12);
    }}
    .range-circle span {{
      position: absolute;
      left: 50%;
      top: 0;
      transform: translate(-50%, -120%);
      padding: 2px 6px;
      border-radius: 999px;
      background: rgba(17, 19, 23, .8);
      color: #ffe887;
      border: 1px solid rgba(255, 220, 90, .55);
      font-size: 11px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .drawn-tree .trunk {{
      position: absolute; left: 45%; bottom: 5%; width: 14%; height: 38%;
      background: #6e4420; border-radius: 40%;
    }}
    .drawn-tree .leaf {{
      position: absolute; border-radius: 50%; background: radial-gradient(circle at 35% 30%, #79c653, #2e7d2c 72%);
      box-shadow: inset -5px -7px 0 rgba(22, 83, 28, .28);
    }}
    .drawn-tree .leaf.a {{ left: 10%; top: 20%; width: 58%; height: 58%; }}
    .drawn-tree .leaf.b {{ right: 8%; top: 12%; width: 58%; height: 58%; }}
    .drawn-tree .leaf.c {{ left: 24%; top: 0; width: 56%; height: 56%; }}
    .drawn-bush span {{
      position: absolute; border-radius: 50%; background: radial-gradient(circle at 35% 30%, #9ad66b, #438b30 70%);
      box-shadow: inset -4px -4px 0 rgba(34, 96, 31, .28);
    }}
    .drawn-bush .a {{ left: 4%; top: 32%; width: 48%; height: 58%; }}
    .drawn-bush .b {{ left: 28%; top: 8%; width: 52%; height: 66%; }}
    .drawn-bush .c {{ right: 2%; top: 30%; width: 48%; height: 58%; }}
    .drawn-warehouse {{
      border-radius: 8px; background: linear-gradient(#bb6f34, #7d4728);
      border: 3px solid #54331f; box-shadow: inset 0 -10px 0 rgba(0,0,0,.14);
    }}
    .drawn-warehouse::before {{
      content: ""; position: absolute; left: 10%; right: 10%; top: -22%; height: 42%;
      background: linear-gradient(135deg, #d69541, #8f4e25); transform: skewX(-18deg);
      border: 3px solid #54331f; border-bottom: 0; border-radius: 6px 6px 0 0;
    }}
    .drawn-warehouse::after {{
      content: ""; position: absolute; left: 38%; bottom: 0; width: 24%; height: 48%;
      background: #3d2c24; border: 2px solid #211713; border-bottom: 0;
    }}
    .drawn-car {{
      position: absolute;
      inset: 12% 4%;
      border-radius: 10px 14px 12px 12px;
      background: linear-gradient(#6fa4dd, #285684);
      border: 2px solid #172838;
      box-shadow: inset 0 -7px 0 rgba(0,0,0,.18);
    }}
    .drawn-car::before {{
      content: "";
      position: absolute;
      left: 23%;
      right: 23%;
      top: 10%;
      height: 34%;
      border-radius: 8px 8px 3px 3px;
      background: linear-gradient(#d9f7ff, #80aebc);
      border: 1px solid rgba(25,38,44,.8);
    }}
    .drawn-car::after {{
      content: "";
      position: absolute;
      left: 9%;
      right: 9%;
      bottom: -16%;
      height: 23%;
      background:
        radial-gradient(circle at 15% 50%, #171b1f 0 7px, transparent 8px),
        radial-gradient(circle at 85% 50%, #171b1f 0 7px, transparent 8px);
    }}
    .drawn-car.red {{ background: linear-gradient(#d7655d, #7e2f31); }}
    .drawn-car.yellow {{ background: linear-gradient(#f0c35b, #a87923); }}
    .drawn-car.dark {{ background: linear-gradient(#5b636d, #252d36); }}
    .drawn-lab {{
      border-radius: 8px; background: linear-gradient(#9fb8c7, #4f7488);
      border: 3px solid #293f4c;
    }}
    .drawn-lab::before {{
      content: ""; position: absolute; left: 12%; top: 18%; width: 20%; height: 22%; background: #c8f2ff; border: 2px solid #31576a;
      box-shadow: 34px 0 0 #c8f2ff, 68px 0 0 #c8f2ff;
    }}
    .drawn-console {{
      border-radius: 8px;
      background: linear-gradient(#6e8b99, #2d4350);
      border: 3px solid #182832;
      box-shadow: inset 0 -8px 0 rgba(0,0,0,.2);
    }}
    .drawn-console::before {{
      content: "";
      position: absolute;
      left: 15%;
      right: 15%;
      top: 14%;
      height: 30%;
      border-radius: 4px;
      background:
        radial-gradient(circle at 22% 50%, #85f5ff 0 4px, transparent 5px),
        radial-gradient(circle at 50% 50%, #ffd86b 0 3px, transparent 4px),
        radial-gradient(circle at 76% 50%, #ff7380 0 3px, transparent 4px),
        linear-gradient(#15242c, #0f171d);
      border: 1px solid #7bc5d2;
    }}
    .drawn-tank {{
      border-radius: 50%;
      background: radial-gradient(circle at 35% 25%, #bafaff, #4e9fb5 58%, #2f6072 100%);
      border: 3px solid #173642;
      box-shadow: inset -8px -10px 0 rgba(0,0,0,.18);
    }}
    .drawn-tank::after {{
      content: "";
      position: absolute;
      left: 48%;
      top: -10%;
      width: 8%;
      height: 120%;
      background: rgba(218, 253, 255, .35);
      transform: rotate(28deg);
    }}
    .drawn-crate {{
      border-radius: 5px;
      background:
        linear-gradient(45deg, transparent 45%, rgba(70,42,24,.55) 46% 54%, transparent 55%),
        linear-gradient(-45deg, transparent 45%, rgba(70,42,24,.55) 46% 54%, transparent 55%),
        linear-gradient(#b8793e, #72431f);
      border: 3px solid #4a2c18;
      box-shadow: inset 0 -7px 0 rgba(0,0,0,.16);
    }}
    .finish-house-object {{ z-index: 3; }}
    .drawn-home {{
      position: relative;
      width: 100%;
      height: 100%;
      filter: drop-shadow(0 7px 5px rgba(0,0,0,.32));
    }}
    .drawn-home::before {{
      content: "";
      position: absolute;
      left: 12%;
      right: 12%;
      bottom: 0;
      height: 58%;
      border-radius: 7px 7px 5px 5px;
      background:
        linear-gradient(90deg, transparent 0 12%, rgba(90,52,28,.7) 13% 28%, transparent 29% 100%),
        linear-gradient(#f2c67a, #a96c36);
      border: 2px solid #5a331f;
    }}
    .drawn-home::after {{
      content: "";
      position: absolute;
      left: 4%;
      right: 4%;
      top: 2%;
      height: 52%;
      clip-path: polygon(50% 0, 100% 82%, 0 82%);
      background: linear-gradient(#b94b3d, #6e241f);
      border-radius: 5px;
    }}
    .drawn-rocks span {{ position: absolute; border-radius: 50%; background: linear-gradient(#c7c2aa, #7c7666); border: 1px solid #575247; }}
    .drawn-rocks .a {{ left: 18%; top: 42%; width: 34%; height: 30%; }}
    .drawn-rocks .b {{ left: 48%; top: 24%; width: 38%; height: 36%; }}
    .drawn-rocks .c {{ left: 38%; top: 58%; width: 28%; height: 24%; }}
    @keyframes shotFlash {{
      from {{ opacity: 1; filter: brightness(1.4); }}
      to {{ opacity: 0; filter: brightness(.6); }}
    }}
    @keyframes noticePop {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes rewardRise {{
      from {{ opacity: 0; transform: translate(-50%, -20%); }}
      18% {{ opacity: 1; }}
      to {{ opacity: 0; transform: translate(-50%, -145%); }}
    }}
    .modal-backdrop {{
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 18px;
      background: rgba(0, 0, 0, .58);
      z-index: 3000;
    }}
    .modal-backdrop.open {{ display: flex; }}
    .modal {{ width: min(560px, 100%); max-height: 88vh; overflow: auto; }}
    .wide-modal {{ width: min(820px, 100%); }}
    .modal-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }}
    .modal-head h2 {{ margin: 0; }}
    #rulesText {{ white-space: pre-line; }}
    .tower-list {{ display: grid; gap: 8px; }}
    .tower-choice {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      width: 100%;
      text-align: left;
      background: var(--panel-2);
      border-color: var(--line);
      color: var(--text);
    }}
    .tower-choice small {{ display: block; color: var(--muted); font-weight: 500; margin-top: 2px; }}
    .tower-choice.selected {{ border-color: var(--accent); background: #302817; }}
    .stat-list {{ display: grid; grid-template-columns: 1fr auto; gap: 6px 12px; margin-top: 10px; }}
    .stat-list span {{ color: var(--muted); }}
    .stat-list b {{ text-align: right; }}
    .diff-up {{ color: var(--accent-2); }}
    .diff-same {{ color: var(--muted); }}
    .main-menu {{ display: grid; gap: 18px; }}
    .menu-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: center;
      background:
        linear-gradient(135deg, rgba(255, 197, 73, .14), transparent 34%),
        linear-gradient(160deg, var(--panel), #151b24);
    }}
    .menu-hero h1 {{ margin-bottom: 6px; }}
    .eyebrow {{
      margin: 0 0 4px;
      color: var(--accent);
      font-weight: 900;
      text-transform: uppercase;
      letter-spacing: 0;
      font-size: 12px;
    }}
    .menu-actions {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }}
    .menu-section {{ display: grid; gap: 12px; }}
    .section-head {{
      display: flex;
      gap: 12px;
      justify-content: space-between;
      align-items: end;
      flex-wrap: wrap;
    }}
    .section-head h2 {{ margin: 0; }}
    .menu-panels {{ display: grid; grid-template-columns: minmax(280px, .95fr) minmax(320px, 1.05fr); gap: 14px; align-items: start; }}
    .menu-panel {{ display: grid; gap: 12px; }}
    .rules-text {{ white-space: pre-line; margin: 0; }}
    .zombie-type-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }}
    .zombie-type-card {{
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
    }}
    .zombie-type-card h3 {{ margin: 0 0 6px; font-size: 16px; }}
    .zombie-type-card .stat-list {{ margin-top: 0; }}
    .history-tools {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }}
    .history-tools select, .history-tools input {{ width: auto; min-width: 180px; }}
    .history-tabs {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
    .history-tabs button.selected {{ border-color: var(--accent); color: var(--accent); background: rgba(255, 197, 73, .08); }}
    .current-player-row {{ background: rgba(96, 210, 138, .08); }}
    .effect-choice-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-top: 12px; }}
    .effect-card {{ display: grid; gap: 8px; text-align: left; background: var(--panel-2); border-color: var(--line); color: var(--text); }}
    .effect-card b {{ font-size: 18px; }}
    .effect-card small {{ color: var(--muted); font-weight: 500; line-height: 1.35; }}
    .effect-list {{ display: grid; gap: 8px; margin-top: 12px; }}
    .effect-pill {{ padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: rgba(255,255,255,.04); }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }}
    .map-card {{ display: grid; gap: 12px; }}
    .map-preview {{
      position: relative;
      height: 190px;
      border-radius: 8px;
      overflow: hidden;
      background:
        radial-gradient(circle at 18% 18%, rgba(78, 131, 41, .9) 0 12px, transparent 13px),
        radial-gradient(circle at 82% 22%, rgba(55, 114, 46, .9) 0 16px, transparent 17px),
        linear-gradient(135deg, #79a943, #4f8d35 42%, #6fa33c);
      box-shadow: inset 0 0 0 2px rgba(18, 42, 22, .7);
    }}
    .map-preview.map-warehouse {{
      background:
        linear-gradient(90deg, rgba(255,255,255,.12) 0 2px, transparent 2px 56px),
        linear-gradient(0deg, rgba(255,255,255,.08) 0 1px, transparent 1px 34px),
        linear-gradient(135deg, #5d6269, #444950 52%, #6a6f75);
      box-shadow: inset 0 0 0 2px rgba(33, 36, 40, .8);
    }}
    .map-preview.map-lab {{
      background:
        linear-gradient(90deg, rgba(116, 219, 230, .09) 0 2px, transparent 2px 48px),
        linear-gradient(0deg, rgba(255,255,255,.07) 0 1px, transparent 1px 28px),
        linear-gradient(135deg, #283641, #1e2832 50%, #344654);
      box-shadow: inset 0 0 0 2px rgba(103, 154, 169, .45);
    }}
    .map-preview svg {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    .preview-road-border {{ fill: none; stroke: rgba(105, 76, 36, .9); stroke-width: 12; stroke-linecap: round; stroke-linejoin: round; }}
    .preview-road-main {{ fill: none; stroke: #d7b46c; stroke-width: 8; stroke-linecap: round; stroke-linejoin: round; }}
    .map-preview.map-warehouse .preview-road-border {{ stroke: #34383d; }}
    .map-preview.map-warehouse .preview-road-main {{ stroke: #8b9198; }}
    .map-preview.map-lab .preview-road-border {{ stroke: #1a222b; }}
    .map-preview.map-lab .preview-road-main {{ stroke: #99aab5; }}
    .preview-build {{
      position: absolute;
      width: 18px;
      height: 18px;
      transform: translate(-50%, -50%);
      border-radius: 50%;
      background: radial-gradient(circle, #d7e2ea 0 38%, #62707a 40% 62%, #303b40 64%);
      border: 1px solid #c6d7e2;
      box-shadow: 0 2px 4px rgba(0,0,0,.3);
    }}
    .preview-decor {{ position: absolute; transform: translate(-50%, -50%); filter: drop-shadow(0 5px 3px rgba(0,0,0,.32)); }}
    .preview-decor > div {{ position: absolute; inset: 0; }}
    .mini .cell {{ width: 18px; border-radius: 3px; font-size: 10px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; font-size: 12px; }}
    .muted {{ color: var(--muted); }}
    .inline-form {{ display: grid; grid-template-columns: 1fr 1fr auto; gap: 8px; align-items: end; }}
    @media (max-width: 900px) {{
      .game-layout, .auth {{ grid-template-columns: 1fr; }}
      .menu-hero, .menu-panels {{ grid-template-columns: 1fr; }}
      .menu-actions {{ justify-content: flex-start; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      main {{ padding: 16px; }}
      .topbar {{ padding: 14px 16px; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">Zombie Defense</a>
    <nav class="userbox">{user_html}</nav>
  </header>
  <main>
    {notice_html}
    {body}
  </main>
</body>
</html>""".encode("utf-8")


def board_html(board: list[list[dict[str, Any]]], mini: bool = False) -> str:
    if not board:
        return ""
    width = len(board[0])
    cells = []
    for line in board:
        for cell in line:
            label = esc(cell["label"])
            title = esc(cell["title"])
            cells.append(f'<div class="cell {esc(cell["kind"])}" title="{title}">{label}</div>')
    return f'<div class="board {"mini" if mini else ""}" style="grid-template-columns: repeat({width}, 1fr)">{"".join(cells)}</div>'


def decoration_markup(item: dict[str, Any]) -> str:
    kind = str(item.get("kind", "tree"))
    if kind == "warehouse":
        return '<div class="drawn-warehouse"></div>'
    if kind == "car":
        return f'<div class="drawn-car {esc(item.get("variant", ""))}"></div>'
    if kind == "lab":
        return '<div class="drawn-lab"></div>'
    if kind == "console":
        return '<div class="drawn-console"></div>'
    if kind == "tank":
        return '<div class="drawn-tank"></div>'
    if kind == "crate":
        return '<div class="drawn-crate"></div>'
    if kind == "bush":
        return '<div class="drawn-bush"><span class="a"></span><span class="b"></span><span class="c"></span></div>'
    if kind == "rocks":
        return '<div class="drawn-rocks"><span class="a"></span><span class="b"></span><span class="c"></span></div>'
    return '<div class="drawn-tree"><span class="trunk"></span><span class="leaf a"></span><span class="leaf b"></span><span class="leaf c"></span></div>'


def map_preview_html(item: dict[str, Any]) -> str:
    map_id = int(item["map_id"])
    width = int(item["map_width"])
    height = int(item["map_height"])
    cell = 18
    route = route_rows(map_id)
    path = " ".join(
        f'{"M" if index == 0 else "L"} {int(point["x"]) * cell + cell / 2:.1f} {int(point["y"]) * cell + cell / 2:.1f}'
        for index, point in enumerate(route)
    )
    build_markers = []
    for point in build_point_rows_for_map(map_id):
        left = (float(point["x"]) + 0.5) / width * 100
        top = (float(point["y"]) + 0.5) / height * 100
        build_markers.append(f'<span class="preview-build" style="left:{left:.2f}%;top:{top:.2f}%"></span>')

    decor_items = []
    for decor in map_decorations(map_id):
        left = (float(decor["x"]) + 0.5) / width * 100
        top = (float(decor["y"]) + 0.5) / height * 100
        decor_width = float(decor.get("w", 1)) / width * 100
        decor_height = float(decor.get("h", 1)) / height * 100
        decor_items.append(
            f'<span class="preview-decor" style="left:{left:.2f}%;top:{top:.2f}%;width:{decor_width:.2f}%;height:{decor_height:.2f}%">{decoration_markup(decor)}</span>'
        )

    extra_class = " map-warehouse" if map_id == 1 else " map-lab" if map_id == 3 else ""
    return f"""
      <div class="map-preview{extra_class}" aria-label="{esc(item["map_name"])}">
        <svg viewBox="0 0 {width * cell} {height * cell}" preserveAspectRatio="none" aria-hidden="true">
          <path class="preview-road-border" d="{esc(path)}"></path>
          <path class="preview-road-main" d="{esc(path)}"></path>
        </svg>
        {"".join(decor_items)}
        {"".join(build_markers)}
      </div>
    """


def login_page(notice: str = "") -> bytes:
    body = """
    <h1>Zombie Defense</h1>
    <div class="auth">
      <section class="panel">
        <h2>Вход</h2>
        <form method="post" action="/login">
          <label>Имя игрока</label>
          <input name="player_name" required autocomplete="username">
          <label>Пароль</label>
          <input name="password" type="password" required autocomplete="current-password">
          <p><button type="submit">Войти</button></p>
        </form>
      </section>
      <section class="panel">
        <h2>Регистрация</h2>
        <form method="post" action="/register">
          <label>Имя игрока</label>
          <input name="player_name" required autocomplete="username">
          <label>Пароль</label>
          <input name="password" type="password" required autocomplete="new-password">
          <p><button type="submit">Создать игрока</button></p>
        </form>
      </section>
    </div>
    """
    return layout("Вход", body, None, notice)


def home_page(current_user: dict[str, Any], notice: str = "") -> bytes:
    player_id = int(current_user["player_id"])
    current = active_session(player_id)
    latest = latest_session(player_id)

    cards = []
    for item in maps_with_preview():
        if current:
            start_control = '<button type="button" disabled>Идёт партия</button>'
        else:
            start_control = f"""
              <form method="post" action="/start">
                <input type="hidden" name="map_id" value="{int(item["map_id"])}">
                <button type="submit">Начать</button>
              </form>
            """
        cards.append(
            f"""
            <section class="panel map-card">
              <div>
                <h2>{esc(item["map_name"])}</h2>
                <div class="muted">{int(item["map_width"])}x{int(item["map_height"])} · {esc(item["difficulty"])} · деньги {int(item["start_money"])} · база {int(item["start_base_health"])}</div>
              </div>
              {item["preview"]}
              {start_control}
            </section>
            """
        )
    active_html = ""
    if current:
        active_html = f'<p><a class="button" href="/game">Продолжить партию #{int(current["session_id"])}</a></p>'
    latest_html = ""
    if latest and latest["status"] != "running":
        latest_html = f'<p class="muted">Последняя партия #{int(latest["session_id"])} завершена со статусом {esc(latest["status"])}.</p>'
    body = f"""
      <div class="main-menu">
        <section class="panel menu-hero">
          <div>
            <p class="eyebrow">Главное меню</p>
            <h1>Zombie Defense</h1>
            <p class="muted">Выбери карту, проверь правила, сравни результаты игроков и используй ежедневный выбор.</p>
            {active_html}
            {latest_html}
          </div>
          <div class="menu-actions">
            <button type="button" class="ghost" id="openRulesBtn">Правила</button>
            <button type="button" class="ghost" id="openHistoryBtn">Результаты игры</button>
            <button type="button" class="ghost" id="openTreatBtn">Сладость или гадость</button>
          </div>
        </section>
        <section class="menu-section" id="mapsPanel">
          <div class="section-head">
            <h2>Карты</h2>
            <span class="muted">Выбор стартовой позиции и сложности</span>
          </div>
          <div class="cards">{"".join(cards)}</div>
        </section>
        <div class="modal-backdrop" id="menuRulesModal">
          <section class="panel modal wide-modal">
            <div class="modal-head">
              <h2>Правила</h2>
              <button type="button" class="ghost" id="closeRulesBtn">Закрыть</button>
            </div>
            {rules_html()}
          </section>
        </div>
        <div class="modal-backdrop" id="menuHistoryModal">
          <section class="panel modal wide-modal">
            <div class="modal-head">
              <h2>Результаты игры</h2>
              <button type="button" class="ghost" id="closeHistoryBtn">Закрыть</button>
            </div>
            {results_html(player_id, str(current_user["player_name"]))}
          </section>
        </div>
        <div class="modal-backdrop" id="menuTreatModal">
          <section class="panel modal wide-modal">
            <div class="modal-head">
              <h2>Сладость или гадость</h2>
              <button type="button" class="ghost" id="closeTreatBtn">Закрыть</button>
            </div>
            {treat_html(player_id)}
          </section>
        </div>
      </div>
      {menu_script()}
    """
    return layout("Выбор карты", body, current_user, notice)


def game_page(current_user: dict[str, Any], session_id: int, notice: str = "") -> bytes:
    player_id = int(current_user["player_id"])
    status = session_status(session_id, player_id)
    if not status:
        return home_page(current_user, "Сессия не найдена.")

    body = f"""
      <div class="grid game-layout">
        <section class="panel">
          <h1 id="mapTitle">{esc(status["map_name"])}</h1>
          <div class="stats" id="stats">
            <div class="stat"><b>-</b><span>База</span></div>
            <div class="stat"><b>-</b><span>Деньги</span></div>
            <div class="stat"><b>-</b><span>Волна</span></div>
            <div class="stat"><b>-</b><span>Время</span></div>
            <div class="stat"><b>-</b><span>Зомби</span></div>
            <div class="stat"><b>-</b><span>Убито</span></div>
          </div>
          <div class="notice game-notice" id="gameNotice" hidden></div>
          <div class="board-wrap">
            <div class="playfield" id="playfield">
              <div class="scenery forest-band" id="forestScenery"></div>
              <div class="scenery river" id="riverScenery"></div>
              <div class="scenery pond" id="pondScenery"></div>
              <svg class="road-layer" id="roadSvg" preserveAspectRatio="none" aria-hidden="true">
                <path class="road-border" id="roadBorder"></path>
                <path class="road-main" id="roadMain"></path>
                <path class="road-highlight" id="roadHighlight"></path>
              </svg>
              <div class="build-clear-layer" id="buildClearLayer"></div>
              <div class="decor-layer" id="decorLayer"></div>
              <div class="board" id="gameBoard"></div>
              <div class="range-layer" id="rangeLayer"></div>
              <div class="shot-layer" id="shotLayer"></div>
              <div class="reward-layer" id="rewardLayer"></div>
            </div>
          </div>
          <div class="actions">
            <button type="button" id="pauseBtn" class="ghost">Пауза</button>
            <button type="button" id="surrenderBtn" class="danger">Сдаться</button>
            <a class="ghost" href="/">Карты</a>
          </div>
        </section>
        <aside class="grid">
          <section class="panel">
            <h2>Башни</h2>
            <div id="towersPanel"><p class="muted">Башни появятся после строительства.</p></div>
            <div id="towerDetails"><p class="muted">Нажми на башню здесь или на поле, чтобы увидеть характеристики.</p></div>
          </section>
        </aside>
      </div>
      <div class="modal-backdrop" id="towerModal">
        <section class="panel modal">
          <h2>Выбор башни</h2>
          <p class="muted" id="modalPoint"></p>
          <div class="tower-list" id="towerList"></div>
          <p><button type="button" class="ghost" id="closeModal">Закрыть</button></p>
        </section>
      </div>
      <div class="modal-backdrop" id="resultModal">
        <section class="panel modal">
          <h2 id="resultTitle">Игра завершена</h2>
          <p class="muted" id="resultText"></p>
          <p><a class="button" href="/">Выйти в главное меню</a></p>
        </section>
      </div>
      <script>
        const stateUrl = "/api/state";
        let gameState = null;
        let paused = false;
        let selectedBuildPoint = null;
        let tickInFlight = false;
        let actionInFlight = false;
        let resumeAfterModal = false;
        let selectedTowerId = null;
        const cellSize = 38;
        const fieldPadding = 18;
        let shownResultSessionId = null;

        const boardEl = document.getElementById("gameBoard");
        const playfieldEl = document.getElementById("playfield");
        const shotLayer = document.getElementById("shotLayer");
        const rewardLayer = document.getElementById("rewardLayer");
        const roadSvg = document.getElementById("roadSvg");
        const rangeLayer = document.getElementById("rangeLayer");
        const buildClearLayer = document.getElementById("buildClearLayer");
        const decorLayer = document.getElementById("decorLayer");
        const modal = document.getElementById("towerModal");
        const resultModal = document.getElementById("resultModal");
        const towerList = document.getElementById("towerList");
        const modalPoint = document.getElementById("modalPoint");
        const gameNotice = document.getElementById("gameNotice");
        let noticeTimer = null;
        let tickVersion = 0;
        const zombieTracks = new Map();
        let zombieAnimationFrame = 0;
        const zombieRenderDelayMs = 700;
        const zombieMaxExtrapolateMs = 1200;
        let lastTickAt = 0;
        let renderedBoardSignature = "";
        let renderedPanelSignature = "";

        function escapeHtml(value) {{
          return String(value ?? "").replace(/[&<>"']/g, char => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }}[char]));
        }}

        function postForm(url, data = {{}}) {{
          return fetch(url, {{
            method: "POST",
            headers: {{ "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" }},
            body: new URLSearchParams(data)
          }}).then(jsonOrRedirect);
        }}

        function jsonOrRedirect(response) {{
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.includes("application/json")) {{
            window.location.href = "/";
            return {{ hasSession: false }};
          }}
          return response.json();
        }}

        function updatePauseButton() {{
          document.getElementById("pauseBtn").textContent = paused ? "Продолжить" : "Пауза";
        }}

        function setPaused(value, syncAfterResume = false) {{
          const next = Boolean(value);
          if (paused !== next) tickVersion += 1;
          paused = next;
          if (paused && zombieAnimationFrame) {{
            window.cancelAnimationFrame(zombieAnimationFrame);
            zombieAnimationFrame = 0;
          }}
          if (!paused) {{
            const now = performance.now();
            lastTickAt = now;
            zombieTracks.forEach(track => {{
              const current = track.rendered || track.samples?.[track.samples.length - 1];
              if (current) track.samples = [{{ ...current, at: now }}];
            }});
            startZombieAnimation();
          }}
          updatePauseButton();
          if (!paused && syncAfterResume) refreshState();
        }}

        function showNotice(message) {{
          if (!message) return;
          window.clearTimeout(noticeTimer);
          gameNotice.textContent = message;
          gameNotice.classList.add("error");
          gameNotice.hidden = false;
          noticeTimer = window.setTimeout(() => {{
            gameNotice.hidden = true;
          }}, 3600);
        }}

        function boardSignature(state) {{
          const s = state.session || {{}};
          return JSON.stringify({{
            sessionId: s.session_id,
            status: s.status,
            mapId: s.map_id,
            width: s.map_width,
            height: s.map_height,
            route: (state.route || []).map(point => [point.point_order, point.x, point.y]),
            buildPoints: (state.buildPoints || []).map(point => [point.build_point_id, point.x, point.y, point.tower_name || ""]),
            towers: (state.towers || []).map(tower => [
              tower.placed_tower_id,
              tower.build_point_id,
              tower.tower_code,
              tower.level_no
            ])
          }});
        }}

        function panelSignature(state) {{
          return JSON.stringify({{
            status: state.session && state.session.status,
            shop: (state.shop || []).map(tower => [tower.tower_type_id, tower.cost, tower.damage, tower.range_cells]),
            upgrades: (state.upgrades || []).map(tower => [
              tower.placed_tower_id,
              tower.tower_name,
              tower.level_no,
              tower.damage,
              tower.range_cells,
              tower.fire_rate,
              tower.splash_radius,
              tower.next_name,
              tower.next_cost,
              tower.sell_refund
            ])
          }});
        }}

        async function runAction(callback, resumeAfter = !paused) {{
          if (actionInFlight) return;
          actionInFlight = true;
          setPaused(true);
          try {{
            const state = await callback();
            applyState(state);
          }} finally {{
            actionInFlight = false;
            if (resumeAfter && gameState && gameState.session.status === "running") {{
              setPaused(false, true);
            }}
          }}
        }}

        function cellCenter(x, y) {{
          return {{ left: fieldPadding + x * cellSize + (cellSize - 4) / 2, top: fieldPadding + y * cellSize + (cellSize - 4) / 2 }};
        }}

        function visualCellCenter(x, y, state) {{
          return cellCenter(x, y);
        }}

        function buildPointOffset(x, y, state) {{
          return {{ x: 0, y: 0 }};
        }}

        function clearRange() {{
          rangeLayer.innerHTML = "";
        }}

        function showRangeAt(x, y, range, label = "") {{
          const numericRange = Number(range || 0);
          if (!numericRange) {{
            clearRange();
            return;
          }}
          const pos = cellCenter(Number(x), Number(y));
          const diameter = numericRange * cellSize * 2;
          const text = label ? `${{label}} · радиус ${{numericRange}} кл.` : `Радиус атаки: ${{numericRange}} кл.`;
          rangeLayer.innerHTML = `
            <div class="range-circle" style="left:${{pos.left}}px;top:${{pos.top}}px;width:${{diameter}}px;height:${{diameter}}px">
              <span>${{escapeHtml(text)}}</span>
            </div>
          `;
        }}

        function showBuildRange(buildPoint, tower) {{
          if (!buildPoint || !tower) return clearRange();
          showRangeAt(buildPoint.x, buildPoint.y, tower.range_cells, tower.tower_name);
        }}

        function renderStats(state) {{
          const s = state.session;
          document.getElementById("mapTitle").textContent = s.map_name;
          document.getElementById("stats").innerHTML = `
            <div class="stat"><b>${{s.base_health}}</b><span>База</span></div>
            <div class="stat"><b>${{s.money}}</b><span>Деньги</span></div>
            <div class="stat"><b>${{s.current_wave}}</b><span>Волна</span></div>
            <div class="stat"><b>${{Math.floor(Number(s.simulation_time))}}</b><span>Время</span></div>
            <div class="stat"><b>${{state.zombies.length}}</b><span>Зомби</span></div>
            <div class="stat"><b>${{s.killed_zombies || 0}}</b><span>Убито</span></div>
          `;
        }}

        function renderBoard(state) {{
          const s = state.session;
          const width = Number(s.map_width);
          const height = Number(s.map_height);
          const mapId = Number(s.map_id);
          playfieldEl.classList.toggle("map-warehouse", mapId === 1);
          playfieldEl.classList.toggle("map-lab", mapId === 3);
          document.getElementById("forestScenery").style.display = "none";
          document.getElementById("riverScenery").style.display = "none";
          document.getElementById("pondScenery").style.display = Number(s.map_id) === 2 ? "block" : "none";
          const routeKeys = new Set(state.route.map(p => `${{p.x}},${{p.y}}`));
          const buildByKey = new Map(state.buildPoints.map(p => [`${{p.x}},${{p.y}}`, p]));
          const towerByBuild = new Map(state.towers.map(t => [Number(t.build_point_id), t]));
          boardEl.style.gridTemplateColumns = `repeat(${{width}}, ${{cellSize - 4}}px)`;
          const fieldWidth = width * cellSize + fieldPadding * 2;
          const fieldHeight = height * cellSize + fieldPadding * 2;
          playfieldEl.style.width = `${{fieldWidth}}px`;
          playfieldEl.style.height = `${{fieldHeight}}px`;
          roadSvg.style.width = `${{fieldWidth}}px`;
          roadSvg.style.height = `${{fieldHeight}}px`;
          rangeLayer.style.width = `${{fieldWidth}}px`;
          rangeLayer.style.height = `${{fieldHeight}}px`;
          roadSvg.setAttribute("viewBox", `0 0 ${{fieldWidth}} ${{fieldHeight}}`);
          clearRange();
          renderRoad(state);
          renderBuildClearings(state);
          renderDecorations(state);
          const cells = [];
          for (let y = 0; y < height; y += 1) {{
            for (let x = 0; x < width; x += 1) {{
              const key = `${{x}},${{y}}`;
              const point = buildByKey.get(key);
              const tower = point ? towerByBuild.get(Number(point.build_point_id)) : null;
              let cls = "empty";
              let label = "";
              let title = "";
              let data = "";
              if (routeKeys.has(key)) {{ cls = "route"; label = ""; title = "Путь"; }}
              if (point) {{
                cls = tower ? `tower tower-${{String(tower.tower_code).toLowerCase()}} clickable` : "build active-build clickable";
                label = tower ? ({{GUN:"G", SNIPER:"S", SPLASH:"M"}}[tower.tower_code] || "T") : "+";
                title = tower ? `${{tower.tower_name}} ур. ${{tower.level_no}}` : `Точка #${{point.build_point_id}}`;
                if (!tower && state.session.status === "running") data = `data-build-point="${{point.build_point_id}}"`;
                if (tower && state.session.status === "running") data = `data-placed-tower="${{tower.placed_tower_id}}"`;
              }}
              cells.push(`<div class="cell ${{cls}}" title="${{escapeHtml(title)}}" ${{data}}>${{escapeHtml(label)}}</div>`);
            }}
          }}
          boardEl.innerHTML = cells.join("");
          boardEl.querySelectorAll("[data-build-point]").forEach(cell => {{
            cell.addEventListener("click", () => openTowerModal(Number(cell.dataset.buildPoint)));
          }});
          boardEl.querySelectorAll("[data-placed-tower]").forEach(cell => {{
            cell.addEventListener("click", () => openUpgradeModal(Number(cell.dataset.placedTower)));
          }});
          renderZombies(state);
        }}

        function renderRoad(state) {{
          const points = [...state.route].sort((a, b) => Number(a.point_order) - Number(b.point_order));
          if (!points.length) return;
          const d = points.map((point, index) => {{
            const pos = cellCenter(Number(point.x), Number(point.y));
            return `${{index === 0 ? "M" : "L"}} ${{pos.left}} ${{pos.top}}`;
          }}).join(" ");
          document.getElementById("roadBorder").setAttribute("d", d);
          document.getElementById("roadMain").setAttribute("d", d);
          document.getElementById("roadHighlight").setAttribute("d", d);
        }}

        function renderBuildClearings(state) {{
          buildClearLayer.innerHTML = (state.buildPoints || []).filter(point => !point.tower_name).map(point => {{
            const pos = cellCenter(Number(point.x), Number(point.y));
            return `<span class="build-clear" style="left:${{pos.left}}px;top:${{pos.top}}px"></span>`;
          }}).join("");
        }}

        function decorationMarkup(item) {{
          if (item.kind === "warehouse") return '<div class="drawn-warehouse"></div>';
          if (item.kind === "car") return `<div class="drawn-car ${{escapeHtml(item.variant || "")}}"></div>`;
          if (item.kind === "lab") return '<div class="drawn-lab"></div>';
          if (item.kind === "console") return '<div class="drawn-console"></div>';
          if (item.kind === "tank") return '<div class="drawn-tank"></div>';
          if (item.kind === "crate") return '<div class="drawn-crate"></div>';
          if (item.kind === "bush") return '<div class="drawn-bush"><span class="a"></span><span class="b"></span><span class="c"></span></div>';
          if (item.kind === "rocks") return '<div class="drawn-rocks"><span class="a"></span><span class="b"></span><span class="c"></span></div>';
          return '<div class="drawn-tree"><span class="trunk"></span><span class="leaf a"></span><span class="leaf b"></span><span class="leaf c"></span></div>';
        }}

        function renderDecorations(state) {{
          const decorations = (state.decorations || []).map(item => {{
            const pos = cellCenter(Number(item.x), Number(item.y));
            const width = Math.max(28, Number(item.w || 1) * cellSize);
            const height = Math.max(24, Number(item.h || 1) * cellSize);
            return `<div class="decor-object" title="${{escapeHtml(item.title || "")}}" style="left:${{pos.left}}px;top:${{pos.top}}px;width:${{width}}px;height:${{height}}px">${{decorationMarkup(item)}}</div>`;
          }});
          const route = [...(state.route || [])].sort((a, b) => Number(a.point_order) - Number(b.point_order));
          if (route.length) {{
            const finish = route[route.length - 1];
            const pos = cellCenter(Number(finish.x), Number(finish.y));
            decorations.push(`<div class="decor-object finish-house-object" title="База" style="left:${{pos.left}}px;top:${{pos.top - 6}}px;width:${{cellSize * 1.18}}px;height:${{cellSize * 1.05}}px"><div class="drawn-home"></div></div>`);
          }}
          decorLayer.innerHTML = decorations.join("");
        }}

        function setZombiePosition(piece, left, top) {{
          piece.style.setProperty("--zombie-x", `${{left}}px`);
          piece.style.setProperty("--zombie-y", `${{top}}px`);
        }}

        function routePoints() {{
          return [...(gameState?.route || [])].sort((a, b) => Number(a.point_order) - Number(b.point_order));
        }}

        function finalSegmentVisualProgress(points, index) {{
          if (index !== points.length - 2) return 0.99;
          const current = points[index];
          const next = points[index + 1];
          const segmentLen = Math.max(0.001, Math.hypot(Number(next.x) - Number(current.x), Number(next.y) - Number(current.y)));
          return Math.max(0, Math.min(0.94, 1 - 0.85 / segmentLen));
        }}

        function zombieSampleFromState(zombie, now) {{
          const points = routePoints();
          const routeOrder = Number(zombie.route_order || 1);
          const segmentProgress = Number(zombie.segment_progress || 0);
          let x = Number(zombie.x);
          let y = Number(zombie.y);
          const index = points.findIndex(point => Number(point.point_order) === routeOrder);
          const finalVisualProgress = finalSegmentVisualProgress(points, index);
          if (index === points.length - 2 && segmentProgress > finalVisualProgress) {{
            const routeX = Number(zombie.route_x ?? zombie.x);
            const routeY = Number(zombie.route_y ?? zombie.y);
            const nextX = Number(zombie.next_x ?? zombie.x);
            const nextY = Number(zombie.next_y ?? zombie.y);
            x = routeX + (nextX - routeX) * finalVisualProgress;
            y = routeY + (nextY - routeY) * finalVisualProgress;
          }}
          const serverPos = cellCenter(x, y);
          return {{
            left: serverPos.left,
            top: serverPos.top,
            at: now,
            routeOrder,
            segmentProgress,
            routeX: Number(zombie.route_x ?? zombie.x),
            routeY: Number(zombie.route_y ?? zombie.y),
            nextX: Number(zombie.next_x ?? zombie.x),
            nextY: Number(zombie.next_y ?? zombie.y),
            baseSpeed: Number(zombie.base_speed || 0)
          }};
        }}

        function advanceZombieSample(sample, elapsedMs) {{
          const speed = Number(sample.baseSpeed || 0);
          if (!speed || elapsedMs <= 0) return {{ ...sample, at: sample.at + Math.max(0, elapsedMs) }};
          const points = routePoints();
          if (points.length < 2) return {{ ...sample, at: sample.at + elapsedMs }};
          let index = points.findIndex(point => Number(point.point_order) === Number(sample.routeOrder));
          if (index < 0) index = Math.max(0, points.findIndex(point => Number(point.x) === Number(sample.routeX) && Number(point.y) === Number(sample.routeY)));
          if (index < 0 || index >= points.length - 1) return {{ ...sample, at: sample.at + elapsedMs }};

          let progress = Math.max(0, Math.min(0.99, Number(sample.segmentProgress || 0)));
          let distanceLeft = speed * (elapsedMs / 1000);

          while (distanceLeft > 0 && index < points.length - 1) {{
            const current = points[index];
            const next = points[index + 1];
            const x1 = Number(current.x);
            const y1 = Number(current.y);
            const x2 = Number(next.x);
            const y2 = Number(next.y);
            const segmentLen = Math.max(0.001, Math.hypot(x2 - x1, y2 - y1));
            const distanceToNext = (1 - progress) * segmentLen;
            const finalVisualProgress = finalSegmentVisualProgress(points, index);
            if (index === points.length - 2) {{
              const distanceToVisualEnd = Math.max(0, (finalVisualProgress - progress) * segmentLen);
              if (distanceLeft >= distanceToVisualEnd) {{
                progress = finalVisualProgress;
                distanceLeft = 0;
                break;
              }}
            }}
            if (distanceLeft < distanceToNext) {{
              progress += distanceLeft / segmentLen;
              distanceLeft = 0;
            }} else {{
              distanceLeft -= distanceToNext;
              index += 1;
              progress = 0;
            }}
          }}

          if (index >= points.length - 1) {{
            const last = points[points.length - 1];
            const pos = cellCenter(Number(last.x), Number(last.y));
            return {{ ...sample, left: pos.left, top: pos.top, at: sample.at + elapsedMs }};
          }}

          const current = points[index];
          const next = points[index + 1];
          const x = Number(current.x) + (Number(next.x) - Number(current.x)) * progress;
          const y = Number(current.y) + (Number(next.y) - Number(current.y)) * progress;
          const pos = cellCenter(x, y);
          return {{
            ...sample,
            left: pos.left,
            top: pos.top,
            at: sample.at + elapsedMs,
            routeOrder: Number(current.point_order),
            segmentProgress: progress,
            routeX: Number(current.x),
            routeY: Number(current.y),
            nextX: Number(next.x),
            nextY: Number(next.y)
          }};
        }}

        function interpolateZombieSamples(samples, renderAt) {{
          if (!samples || !samples.length) return null;
          if (samples.length === 1 || renderAt <= samples[0].at) return samples[0];
          for (let index = 0; index < samples.length - 1; index += 1) {{
            const previous = samples[index];
            const current = samples[index + 1];
            if (renderAt > current.at) continue;
            const span = Math.max(1, current.at - previous.at);
            const progress = Math.max(0, Math.min(1, (renderAt - previous.at) / span));
            return {{
              left: previous.left + (current.left - previous.left) * progress,
              top: previous.top + (current.top - previous.top) * progress,
              at: renderAt
            }};
          }}
          const last = samples[samples.length - 1];
          const underrun = renderAt - last.at;
          if (underrun <= 0) return last;
          return advanceZombieSample(last, Math.min(underrun, zombieMaxExtrapolateMs));
        }}

        function trimZombieSamples(samples, now) {{
          while (samples.length > 2 && samples[1].at < now - zombieRenderDelayMs - 700) {{
            samples.shift();
          }}
          if (samples.length > 12) samples.splice(0, samples.length - 12);
          return samples;
        }}

        function addZombieSample(existing, sample, now) {{
          const samples = existing?.samples ? [...existing.samples] : [];
          const last = samples[samples.length - 1];
          if (!last || Math.hypot(last.left - sample.left, last.top - sample.top) > 0.05) {{
            samples.push(sample);
          }} else if (sample.at > last.at) {{
            samples[samples.length - 1] = {{ ...last, at: sample.at }};
          }}
          return trimZombieSamples(samples, now);
        }}

        function interpolatedZombiePosition(track, renderAt) {{
          const pos = interpolateZombieSamples(track.samples, renderAt);
          if (!pos) return null;
          return {{
            left: pos.left,
            top: pos.top,
            at: renderAt
          }};
        }}

        function startZombieAnimation() {{
          if (zombieAnimationFrame || paused) return;
          const step = now => {{
            zombieAnimationFrame = 0;
            if (paused || !gameState || !gameState.session || gameState.session.status !== "running") return;
            const renderAt = now - zombieRenderDelayMs;
            zombieTracks.forEach((track, id) => {{
              const piece = playfieldEl.querySelector(`[data-zombie-id="${{id}}"]`);
              if (!piece) {{
                zombieTracks.delete(id);
                return;
              }}
              const pos = interpolatedZombiePosition(track, renderAt);
              if (!pos) return;
              setZombiePosition(piece, pos.left, pos.top);
              track.rendered = pos;
            }});
            if (zombieTracks.size > 0) {{
              zombieAnimationFrame = window.requestAnimationFrame(step);
            }}
          }};
          zombieAnimationFrame = window.requestAnimationFrame(step);
        }}

        function renderZombies(state) {{
          const seen = new Set();
          const now = performance.now();
          const orderedZombies = [...state.zombies].sort((a, b) => Number(a.active_zombie_id) - Number(b.active_zombie_id));
          for (const zombie of orderedZombies) {{
            const current = zombieSampleFromState(zombie, now);
            const startPos = cellCenter(current.routeX, current.routeY);
            const id = String(zombie.active_zombie_id);
            const typeId = Number(zombie.zombie_type_id);
            const typeClass = typeId === 2 ? " zombie-fast" : typeId === 3 ? " zombie-tank" : " zombie-normal";
            seen.add(id);
            let piece = playfieldEl.querySelector(`[data-zombie-id="${{id}}"]`);
            const isNew = !piece;
            if (!piece) {{
              piece = document.createElement("div");
              piece.className = `zombie-piece${{typeClass}}`;
              piece.dataset.zombieId = id;
              setZombiePosition(piece, startPos.left, startPos.top);
              piece.innerHTML = `
                <div class="hpbar"><span></span></div>
                <div class="zombie-sprite">
                  <span class="arm left"></span><span class="arm right"></span>
                  <span class="leg left"></span><span class="leg right"></span>
                  <span class="body"></span><span class="head"></span>
                </div>
              `;
              playfieldEl.appendChild(piece);
            }} else if (piece.className !== `zombie-piece${{typeClass}}`) {{
              piece.className = `zombie-piece${{typeClass}}`;
            }}
            piece.title = `${{zombie.zombie_type_name}}: ${{zombie.current_health}}/${{zombie.base_health}}`;
            const hp = piece.querySelector(".hpbar span");
            if (hp) hp.style.width = `${{zombie.hp_percent}}%`;
            piece.style.zIndex = String(4 + Math.min(80, Math.round(Number(zombie.y) * 4)));
            const existing = zombieTracks.get(id);
            const samples = isNew
              ? [{{ ...current, left: startPos.left, top: startPos.top, segmentProgress: 0, at: now - zombieRenderDelayMs - 1 }}, current]
              : addZombieSample(existing, current, now);
            if (isNew) {{
              zombieTracks.set(id, {{ samples, rendered: current }});
            }} else {{
              zombieTracks.set(id, {{ samples, rendered: existing?.rendered || current }});
            }}
          }}
          playfieldEl.querySelectorAll(".zombie-piece").forEach(item => {{
            if (!seen.has(item.dataset.zombieId)) {{
              zombieTracks.delete(item.dataset.zombieId);
              item.remove();
            }}
          }});
          startZombieAnimation();
        }}

        function renderShots(shots) {{
          shotLayer.innerHTML = "";
          for (const shot of shots || []) {{
            const from = gameState ? visualCellCenter(Number(shot.fromX), Number(shot.fromY), gameState) : cellCenter(Number(shot.fromX), Number(shot.fromY));
            const zombieSprite = playfieldEl.querySelector(`[data-zombie-id="${{String(shot.zombieId)}}"] .zombie-sprite`);
            let to = cellCenter(Number(shot.toX), Number(shot.toY));
            if (zombieSprite) {{
              const spriteRect = zombieSprite.getBoundingClientRect();
              const fieldRect = playfieldEl.getBoundingClientRect();
              to = {{
                left: spriteRect.left - fieldRect.left + spriteRect.width / 2,
                top: spriteRect.top - fieldRect.top + spriteRect.height / 2
              }};
            }}
            const dx = to.left - from.left;
            const dy = to.top - from.top;
            const beam = document.createElement("div");
            beam.className = "shot";
            beam.style.left = `${{from.left}}px`;
            beam.style.top = `${{from.top}}px`;
            beam.style.width = `${{Math.hypot(dx, dy)}}px`;
            beam.style.transform = `rotate(${{Math.atan2(dy, dx)}}rad)`;
            shotLayer.appendChild(beam);
          }}
        }}

        function renderRewards(rewards) {{
          for (const reward of rewards || []) {{
            const pos = cellCenter(Number(reward.x), Number(reward.y));
            const pop = document.createElement("div");
            pop.className = "reward-pop";
            pop.textContent = `+${{reward.reward}}`;
            pop.style.left = `${{pos.left}}px`;
            pop.style.top = `${{pos.top - 8}}px`;
            rewardLayer.appendChild(pop);
            window.setTimeout(() => pop.remove(), 1200);
          }}
        }}

        function renderPanels(state) {{
          document.getElementById("towersPanel").innerHTML = state.upgrades.length ? state.upgrades.map(item => `
            <button type="button" class="tower-choice" data-inspect-tower="${{item.placed_tower_id}}">
              <span>${{escapeHtml(item.tower_name)}} <small>(${{item.x}}, ${{item.y}}), уровень ${{item.level_no}}</small></span>
              <b>${{item.next_name ? "↑" : "max"}}</b>
            </button>
          `).join("") : `<p class="muted">Пока башен нет. Нажми на клетку + на поле.</p>`;
          document.querySelectorAll("[data-inspect-tower]").forEach(button => {{
            button.addEventListener("click", async () => {{
              showTowerDetails(Number(button.dataset.inspectTower));
            }});
          }});
          if (selectedTowerId && state.upgrades.some(item => Number(item.placed_tower_id) === selectedTowerId)) {{
            showTowerDetails(selectedTowerId, false);
          }} else {{
            document.getElementById("towerDetails").innerHTML = `<p class="muted">Нажми на башню здесь или на поле, чтобы увидеть характеристики.</p>`;
          }}
        }}

        function formatStat(value, suffix = "") {{
          if (value === null || value === undefined) return "-";
          const number = Number(value);
          if (Number.isNaN(number)) return escapeHtml(value);
          return `${{Number.isInteger(number) ? number : number.toFixed(2)}}${{suffix}}`;
        }}

        function statDiff(label, current, next, suffix = "") {{
          if (next === null || next === undefined) {{
            return `<span>${{label}}</span><b>${{formatStat(current, suffix)}}</b>`;
          }}
          const same = Number(current) === Number(next);
          if (same) {{
            return `<span>${{label}}</span><b>${{formatStat(current, suffix)}}</b>`;
          }}
          return `
            <span>${{label}}</span>
            <b>${{formatStat(current, suffix)}} <span class="diff-up">→ ${{formatStat(next, suffix)}}</span></b>
          `;
        }}

        function showTowerDetails(placedTowerId, markSelected = true) {{
          selectedTowerId = placedTowerId;
          const tower = gameState.upgrades.find(item => Number(item.placed_tower_id) === placedTowerId);
          if (!tower) return;
          const hasUpgrade = Boolean(tower.next_name);
          const nextValue = field => hasUpgrade ? tower[field] : null;
          showRangeAt(tower.x, tower.y, tower.range_cells, tower.tower_name);
          if (markSelected) {{
            document.querySelectorAll("[data-inspect-tower]").forEach(item => item.classList.toggle("selected", Number(item.dataset.inspectTower) === placedTowerId));
          }}
          const upgradeButton = hasUpgrade ? `<button type="button" id="sideUpgradeBtn">Улучшить за ${{tower.next_cost}}</button>` : `<p class="muted">Максимальный уровень.</p>`;
          const demolishButton = `<button type="button" class="danger" id="sideDemolishBtn">Снести +${{tower.sell_refund}}</button>`;
          document.getElementById("towerDetails").innerHTML = `
            <h3>${{escapeHtml(tower.tower_name)}} (${{tower.x}}, ${{tower.y}})</h3>
            <div class="stat-list">
              ${{statDiff("Уровень", tower.level_no, hasUpgrade ? Number(tower.level_no) + 1 : null)}}
              ${{statDiff("Урон", tower.damage, nextValue("next_damage"))}}
              ${{statDiff("Дальность", tower.range_cells, nextValue("next_range_cells"))}}
              ${{statDiff("КД выстрела", tower.fire_rate, nextValue("next_fire_rate"), "с")}}
              ${{statDiff("Радиус взрыва", tower.splash_radius, nextValue("next_splash_radius"))}}
              <span>Снос</span><b>+${{tower.sell_refund}}</b>
            </div>
            <p class="actions">${{upgradeButton}}${{demolishButton}}</p>
          `;
          const button = document.getElementById("sideUpgradeBtn");
          if (button) {{
            button.addEventListener("click", () => runAction(() => postForm("/api/upgrade", {{ placed_tower_id: placedTowerId }})));
          }}
          const demolish = document.getElementById("sideDemolishBtn");
          if (demolish) {{
            demolish.addEventListener("click", () => {{
              selectedTowerId = null;
              clearRange();
              runAction(() => postForm("/api/demolish", {{ placed_tower_id: placedTowerId }}));
            }});
          }}
        }}

        function openTowerModal(buildPointId) {{
          resumeAfterModal = !paused;
          setPaused(true);
          selectedBuildPoint = buildPointId;
          const point = gameState.buildPoints.find(item => Number(item.build_point_id) === buildPointId);
          modalPoint.textContent = point ? `Точка #${{point.build_point_id}} (${{point.x}}, ${{point.y}})` : "";
          towerList.innerHTML = gameState.shop.map(tower => `
            <button type="button" class="tower-choice" data-tower="${{tower.tower_type_id}}">
              <span>${{escapeHtml(tower.tower_name)}} <small>урон ${{tower.damage}}, дальность ${{tower.range_cells}}, цена ${{tower.cost}}</small></span>
              <b>${{tower.cost}}</b>
            </button>
          `).join("");
          showBuildRange(point, gameState.shop[0]);
          towerList.querySelectorAll("[data-tower]").forEach(button => {{
            const previewTower = gameState.shop.find(item => Number(item.tower_type_id) === Number(button.dataset.tower));
            button.addEventListener("mouseenter", () => showBuildRange(point, previewTower));
            button.addEventListener("focus", () => showBuildRange(point, previewTower));
            button.addEventListener("click", async () => {{
              const pointToBuild = selectedBuildPoint;
              modal.classList.remove("open");
              selectedBuildPoint = null;
              clearRange();
              await runAction(() => postForm("/api/build", {{ build_point_id: pointToBuild, tower_type_id: button.dataset.tower }}), resumeAfterModal);
            }});
          }});
          modal.classList.add("open");
        }}

        function openUpgradeModal(placedTowerId) {{
          resumeAfterModal = !paused;
          setPaused(true);
          const tower = gameState.upgrades.find(item => Number(item.placed_tower_id) === placedTowerId);
          if (!tower) return;
          selectedBuildPoint = null;
          selectedTowerId = placedTowerId;
          showRangeAt(tower.x, tower.y, tower.range_cells, tower.tower_name);
          modalPoint.textContent = `${{tower.tower_name}} (${{tower.x}}, ${{tower.y}})`;
          if (!tower.next_name) {{
            towerList.innerHTML = `
              <p class="muted">Эта башня уже максимального уровня.</p>
              <button type="button" class="tower-choice danger" id="modalDemolishBtn">
                <span>Снести башню <small>Вернётся 50% затрат: ${{tower.sell_refund}}</small></span>
                <b>+${{tower.sell_refund}}</b>
              </button>
            `;
          }} else {{
            towerList.innerHTML = `
              <button type="button" class="tower-choice" id="modalUpgradeBtn">
                <span>Улучшить до ${{escapeHtml(tower.next_name)}} <small>Стоимость: ${{tower.next_cost}}</small></span>
                <b>↑</b>
              </button>
              <button type="button" class="tower-choice danger" id="modalDemolishBtn">
                <span>Снести башню <small>Вернётся 50% затрат: ${{tower.sell_refund}}</small></span>
                <b>+${{tower.sell_refund}}</b>
              </button>
            `;
            document.getElementById("modalUpgradeBtn").addEventListener("click", async () => {{
              modal.classList.remove("open");
              await runAction(() => postForm("/api/upgrade", {{ placed_tower_id: placedTowerId }}), resumeAfterModal);
            }});
          }}
          const demolish = document.getElementById("modalDemolishBtn");
          if (demolish) {{
            demolish.addEventListener("click", async () => {{
              modal.classList.remove("open");
              selectedTowerId = null;
              clearRange();
              await runAction(() => postForm("/api/demolish", {{ placed_tower_id: placedTowerId }}), resumeAfterModal);
            }});
          }}
          modal.classList.add("open");
        }}

        function applyState(state) {{
          if (!state.hasSession) {{
            window.location.href = "/";
            return;
          }}
          gameState = state;
          renderShots(state.shots);
          renderStats(state);
          const nextBoardSignature = boardSignature(state);
          if (nextBoardSignature !== renderedBoardSignature) {{
            renderedBoardSignature = nextBoardSignature;
            renderBoard(state);
          }} else {{
            renderZombies(state);
          }}
          const nextPanelSignature = panelSignature(state);
          if (nextPanelSignature !== renderedPanelSignature) {{
            renderedPanelSignature = nextPanelSignature;
            renderPanels(state);
          }}
          showNotice(state.notice);
          renderRewards(state.rewards);
          if (state.session.status !== "running") {{
            setPaused(true);
            showResult(state);
          }}
          updatePauseButton();
        }}

        function showResult(state) {{
          const sessionId = Number(state.session.session_id);
          if (shownResultSessionId === sessionId) return;
          shownResultSessionId = sessionId;
          const isWin = state.session.status === "win";
          document.getElementById("resultTitle").textContent = isWin ? "Победа!" : "Поражение";
          document.getElementById("resultText").textContent = isWin
            ? `Все волны пройдены. Время: ${{Math.floor(Number(state.session.simulation_time))}} сек.`
            : `База разрушена или партия завершена. Достигнута волна: ${{state.session.current_wave}}.`;
          resultModal.classList.add("open");
        }}

        async function refreshState() {{
          const response = await fetch(stateUrl);
          applyState(await jsonOrRedirect(response));
        }}

        async function tickOnce() {{
          if (!gameState || gameState.session.status !== "running" || tickInFlight || actionInFlight) return;
          const version = tickVersion;
          const now = performance.now();
          if (!lastTickAt) lastTickAt = now - 250;
          const deltaSeconds = Math.max(0.05, Math.min(1.0, (now - lastTickAt) / 1000));
          lastTickAt = now;
          tickInFlight = true;
          try {{
            const state = await postForm("/api/tick", {{ amount: 1, delta: deltaSeconds.toFixed(3) }});
            if (version === tickVersion && !paused) {{
              applyState(state);
            }} else if (state.session && state.session.status !== "running") {{
              applyState(state);
            }}
          }} finally {{
            tickInFlight = false;
          }}
        }}

        document.getElementById("pauseBtn").addEventListener("click", () => {{
          setPaused(!paused, true);
        }});
        document.getElementById("surrenderBtn").addEventListener("click", async () => runAction(() => postForm("/api/surrender")));
        function closeModal() {{
          modal.classList.remove("open");
          selectedBuildPoint = null;
          if (selectedTowerId && gameState && gameState.upgrades.some(item => Number(item.placed_tower_id) === selectedTowerId)) {{
            showTowerDetails(selectedTowerId, false);
          }} else {{
            clearRange();
          }}
          if (resumeAfterModal && gameState && gameState.session.status === "running") {{
            setPaused(false, true);
          }}
        }}
        document.getElementById("closeModal").addEventListener("click", closeModal);
        modal.addEventListener("click", event => {{ if (event.target === modal) closeModal(); }});
        playfieldEl.addEventListener("click", event => {{
          if (modal.classList.contains("open")) return;
          if (event.target.closest("[data-build-point], [data-placed-tower]")) return;
          selectedTowerId = null;
          document.querySelectorAll("[data-inspect-tower]").forEach(item => item.classList.remove("selected"));
          clearRange();
        }});
        setInterval(() => {{ if (!paused) tickOnce(); }}, 250);
        refreshState();
      </script>
    """
    return layout("Игра", body, current_user, "")


def shop_html(session_id: int) -> str:
    tower_options = []
    for tower in shop_rows():
        special = ""
        if float(tower["splash_radius"]) > 0:
            special = f", радиус {tower['splash_radius']}"
        tower_options.append(
            f'<option value="{int(tower["tower_type_id"])}">{esc(tower["tower_name"])} · {int(tower["cost"])} · урон {int(tower["damage"])}{esc(special)}</option>'
        )

    point_options = []
    for point in build_points(session_id):
        busy = point["tower_name"] is not None
        disabled = " disabled" if busy else ""
        label = f'#{int(point["build_point_id"])} ({point["x"]}, {point["y"]})'
        if busy:
            label += f' · занято: {point["tower_name"]}'
        point_options.append(f'<option value="{int(point["build_point_id"])}"{disabled}>{esc(label)}</option>')

    return f"""
      <section class="panel">
        <h2>Построить башню</h2>
        <form class="inline-form" method="post" action="/build">
          <div><label>Башня</label><select name="tower_type_id">{"".join(tower_options)}</select></div>
          <div><label>Точка</label><select name="build_point_id">{"".join(point_options)}</select></div>
          <button type="submit">Купить</button>
        </form>
      </section>
    """


def upgrades_html(session_id: int) -> str:
    options = []
    for tower in placed_towers(session_id):
        if tower["next_name"]:
            label = f'#{int(tower["placed_tower_id"])} {tower["tower_name"]} -> {tower["next_name"]} за {int(tower["next_cost"])}'
            options.append(f'<option value="{int(tower["placed_tower_id"])}">{esc(label)}</option>')
    if not options:
        return '<section class="panel"><h2>Улучшения</h2><p class="muted">Пока нечего улучшать.</p></section>'
    return f"""
      <section class="panel">
        <h2>Улучшения</h2>
        <form class="inline-form" method="post" action="/upgrade" style="grid-template-columns: 1fr auto">
          <div><label>Башня</label><select name="placed_tower_id">{"".join(options)}</select></div>
          <button type="submit">Улучшить</button>
        </form>
      </section>
    """


def zombie_role_text(item: dict[str, Any]) -> str:
    name = str(item["zombie_type_name"]).lower()
    if "быстр" in name:
        return "Бежит быстрее остальных, но имеет меньше здоровья."
    if "танк" in name:
        return "Идёт медленно, зато выдерживает много попаданий."
    return "Обычный враг: средняя скорость и среднее здоровье."


def rules_html() -> str:
    tower_cards = []
    for tower in shop_rows():
        special = ""
        if float(tower["splash_radius"]) > 0:
            special = f" Взрыв задевает врагов рядом с целью: радиус {tower['splash_radius']} клетки."
        tower_cards.append(
            f"""
            <article class="zombie-type-card">
              <h3>{esc(tower["tower_name"])}</h3>
              <p class="muted">Поставь башню на свободную круглую площадку.{esc(special)}</p>
              <div class="stat-list">
                <span>Урон</span><b>{int(tower["damage"])}</b>
                <span>Дальность</span><b>{int(tower["range_cells"])}</b>
                <span>Пауза</span><b>{float(tower["fire_rate"]):.2f} с</b>
                <span>Цена</span><b>{int(tower["cost"])}</b>
              </div>
            </article>
            """
        )

    zombie_cards = []
    for item in zombie_type_rows():
        zombie_cards.append(
            f"""
            <article class="zombie-type-card">
              <h3>{esc(item["zombie_type_name"])}</h3>
              <p class="muted">{esc(zombie_role_text(item))}</p>
              <div class="stat-list">
                <span>HP</span><b>{int(item["base_health"])}</b>
                <span>Скорость</span><b>{float(item["base_speed"]):.2f}</b>
                <span>Награда</span><b>{int(item["reward"])}</b>
              </div>
            </article>
            """
        )

    return f"""
      <div class="menu-panel" id="rulesPanel">
        <span class="muted">Как играть</span>
        <p class="muted rules-text">Зомби идут по дороге к базе. Если зомби дошёл до конца пути, база теряет здоровье. Твоя задача - ставить башни на свободные площадки рядом с дорогой, улучшать их и пережить все волны.</p>
        <p class="muted rules-text">Нажми на свободную площадку, выбери башню и следи за деньгами. Нажми на уже построенную башню или на её строку сбоку, чтобы увидеть характеристики, радиус атаки и улучшение. Чем меньше значение "Пауза", тем чаще башня стреляет.</p>
        <p class="muted rules-text">На поздних волнах у врагов растёт броня: это множитель максимального здоровья. Полоска HP над зомби уже показывает здоровье с учётом текущей волны.</p>
        <h3>Башни</h3>
        <div class="zombie-type-grid">{"".join(tower_cards)}</div>
        <h3>Враги</h3>
        <div class="zombie-type-grid">{"".join(zombie_cards)}</div>
      </div>
    """


def history_html(player_id: int) -> str:
    items = history(player_id)
    lines = []
    for item in items:
        lines.append(
            f"""
            <tr data-result="{esc(item["result_status"])}" data-duration="{float(item["duration"]):.6f}" data-finished="{esc(item["finished_at"])}">
              <td>{esc(item["map_name"])}</td>
              <td>{esc(item["result_status"])}</td>
              <td>{int(item["reached_wave"])}</td>
              <td>{float(item["duration"]):.0f} сек.</td>
            </tr>
            """
        )
    history_body = (
        f'<table id="menuHistoryTable"><thead><tr><th>Карта</th><th>Итог</th><th>Волна</th><th>Время</th></tr></thead><tbody>{"".join(lines)}</tbody></table>'
        if items
        else '<p class="muted">Завершенных партий пока нет.</p>'
    )
    return f"""
      <div class="menu-panel" id="historyPanel">
        <div class="section-head">
          <label class="history-tools">Сортировка
            <select id="menuHistorySort">
              <option value="finished_desc">Сначала новые</option>
              <option value="result">По результату</option>
              <option value="time_asc">По времени: быстрее</option>
              <option value="time_desc">По времени: дольше</option>
            </select>
          </label>
        </div>
        {history_body}
      </div>
    """


def results_html(player_id: int, player_name: str) -> str:
    items = leaderboard()
    map_options = ['<option value="">Все карты</option>']
    for item in menu_maps():
        map_options.append(f'<option value="{int(item["map_id"])}">{esc(item["map_name"])}</option>')

    lines = []
    current_name = player_name.lower()
    for item in items:
        is_current = str(item["player_name"]).lower() == current_name
        row_class = " class=\"current-player-row\"" if is_current else ""
        lines.append(
            f"""
            <tr{row_class}
                data-map="{int(item["map_id"])}"
                data-player="{esc(item["player_name"]).lower()}"
                data-result="{esc(item["result_status"])}"
                data-duration="{float(item["duration"]):.6f}"
                data-finished="{esc(item["finished_at"])}"
                data-place="{int(item["place_no"])}"
                data-current="{1 if is_current else 0}">
              <td>{int(item["place_no"])}</td>
              <td>{esc(item["player_name"])}</td>
              <td>{esc(item["map_name"])}</td>
              <td>{esc(item["result_status"])}</td>
              <td>{int(item["reached_wave"])}</td>
              <td>{float(item["duration"]):.0f} сек.</td>
              <td>{esc(item["finished_at"])}</td>
            </tr>
            """
        )

    body = (
        f"""
        <table id="leaderboardTable">
          <thead><tr><th>Место</th><th>Игрок</th><th>Карта</th><th>Итог</th><th>Волна</th><th>Время</th><th>Дата</th></tr></thead>
          <tbody>{"".join(lines)}</tbody>
        </table>
        """
        if items
        else '<p class="muted">Завершённых партий пока нет.</p>'
    )

    return f"""
      <div class="menu-panel" id="resultsPanel">
        <div class="history-tools">
          <label>Карта
            <select id="leaderboardMapFilter">{"".join(map_options)}</select>
          </label>
          <label>Сортировка
            <select id="leaderboardSort">
              <option value="place">По месту на карте</option>
              <option value="result">Win затем lose</option>
              <option value="time_asc">По времени: быстрее</option>
              <option value="time_desc">По времени: дольше</option>
            </select>
          </label>
        </div>
        <p class="muted">Общая сводка по всем игрокам. Твои результаты подсвечены, а фильтр по карте помогает посмотреть рейтинг на конкретной карте.</p>
        {body}
      </div>
    """


def effect_text(item: dict[str, Any]) -> str:
    value = float(item["effect_value"])
    if item["effect_type"] == "MONEY_BONUS":
        return f'+{int(value)} стартовых монет'
    if item["effect_type"] == "ZOMBIE_SPEED":
        return f'зомби быстрее на {int(round(value * 100))}%'
    return str(item["effect_type"])


def treat_html(player_id: int) -> str:
    status = treat_status(player_id)
    can_sweet = int(status["can_sweet"]) == 1
    can_trick = int(status["can_trick"]) == 1
    cooldown = int(status["cooldown_seconds"] or 0)
    sweet_disabled = "" if can_sweet else " disabled"
    trick_disabled = "" if can_trick else " disabled"
    cooldown_html = ""
    if not can_sweet or not can_trick:
        used = []
        if not can_sweet:
            used.append("сладость")
        if not can_trick:
            used.append("гадость")
        cooldown_html = f'<p class="muted">Сегодня уже использовано: {esc(", ".join(used))}. Сброс лимитов через <b id="treatCooldown" data-seconds="{cooldown}"></b>.</p>'

    effects = []
    for item in active_effects(player_id):
        source = "обратный эффект" if int(item["is_backfire"]) == 1 else f'от {esc(item["source_name"])}'
        effects.append(
            f"""
            <div class="effect-pill">
              <b>{esc(effect_text(item))}</b>
              <span class="muted"> {source}, до {esc(item["expires_at"])}</span>
            </div>
            """
        )
    active_html = (
        f'<div class="effect-list">{"".join(effects)}</div>'
        if effects
        else '<p class="muted">На тебе сейчас нет активных эффектов.</p>'
    )

    return f"""
      <div class="menu-panel">
        <p class="muted">Раз в сутки можно отправить одну сладость и одну гадость. Каждый эффект действует 12 часов. Если выбрать гадость, есть шанс 33%, что она сработает и на тебе.</p>
        {cooldown_html}
        <form method="post" action="/treat">
          <label>Имя игрока
            <input name="target_player_name" placeholder="Введите имя игрока" required>
          </label>
          <div class="effect-choice-grid">
            <button type="submit" name="action_type" value="SWEET" class="effect-card"{sweet_disabled}>
              <b>Сладость</b>
              <small>+100 стартовых монет на каждой карте на 12 часов.</small>
            </button>
            <button type="submit" name="action_type" value="TRICK" class="effect-card danger"{trick_disabled}>
              <b>Гадость</b>
              <small>Зомби на каждой карте двигаются на 15% быстрее в течение 12 часов. Шанс обратного эффекта: 33%.</small>
            </button>
          </div>
        </form>
        <h3>Твои активные эффекты</h3>
        {active_html}
      </div>
    """


def menu_script() -> str:
    return """
      <script>
        function bindMenuModal(openId, modalId, closeId) {
          const openButton = document.getElementById(openId);
          const modal = document.getElementById(modalId);
          const closeButton = document.getElementById(closeId);
          if (!openButton || !modal || !closeButton) return;
          openButton.addEventListener("click", () => modal.classList.add("open"));
          closeButton.addEventListener("click", () => modal.classList.remove("open"));
          modal.addEventListener("click", event => {
            if (event.target === modal) modal.classList.remove("open");
          });
        }
        bindMenuModal("openRulesBtn", "menuRulesModal", "closeRulesBtn");
        bindMenuModal("openHistoryBtn", "menuHistoryModal", "closeHistoryBtn");
        bindMenuModal("openTreatBtn", "menuTreatModal", "closeTreatBtn");

        const historySort = document.getElementById("menuHistorySort");
        const historyTable = document.getElementById("menuHistoryTable");
        if (historySort && historyTable) {
          historySort.addEventListener("change", () => {
            const rows = Array.from(historyTable.tBodies[0].rows);
            const mode = historySort.value;
            rows.sort((a, b) => {
              if (mode === "result") {
                const resultRank = result => result === "win" ? 0 : result === "lose" ? 1 : 2;
                return resultRank(a.dataset.result) - resultRank(b.dataset.result)
                  || String(b.dataset.finished).localeCompare(String(a.dataset.finished));
              }
              if (mode === "time_asc") return Number(a.dataset.duration) - Number(b.dataset.duration);
              if (mode === "time_desc") return Number(b.dataset.duration) - Number(a.dataset.duration);
              return String(b.dataset.finished).localeCompare(String(a.dataset.finished));
            });
            rows.forEach(row => historyTable.tBodies[0].appendChild(row));
          });
        }

        const leaderboardTable = document.getElementById("leaderboardTable");
        const leaderboardMapFilter = document.getElementById("leaderboardMapFilter");
        const leaderboardSort = document.getElementById("leaderboardSort");
        function resultRank(result) {
          return result === "win" ? 0 : result === "lose" ? 1 : 2;
        }
        function updateLeaderboard() {
          if (!leaderboardTable) return;
          const mapId = leaderboardMapFilter ? leaderboardMapFilter.value : "";
          const sortMode = leaderboardSort ? leaderboardSort.value : "place";
          const rows = Array.from(leaderboardTable.tBodies[0].rows);
          rows.forEach(row => {
            const mapMatch = !mapId || row.dataset.map === mapId;
            row.hidden = !mapMatch;
          });
          rows.sort((a, b) => {
            if (sortMode === "result") {
              return resultRank(a.dataset.result) - resultRank(b.dataset.result)
                || Number(a.dataset.place) - Number(b.dataset.place);
            }
            if (sortMode === "time_asc") return Number(a.dataset.duration) - Number(b.dataset.duration);
            if (sortMode === "time_desc") return Number(b.dataset.duration) - Number(a.dataset.duration);
            return Number(a.dataset.map) - Number(b.dataset.map)
              || Number(a.dataset.place) - Number(b.dataset.place);
          });
          rows.forEach(row => leaderboardTable.tBodies[0].appendChild(row));
          let visiblePlace = 1;
          rows.forEach(row => {
            if (!row.hidden && row.cells.length > 0) {
              row.cells[0].textContent = String(visiblePlace);
              visiblePlace += 1;
            }
          });
        }
        [leaderboardMapFilter, leaderboardSort].forEach(element => {
          if (element) element.addEventListener("input", updateLeaderboard);
          if (element) element.addEventListener("change", updateLeaderboard);
        });
        updateLeaderboard();

        const cooldown = document.getElementById("treatCooldown");
        if (cooldown) {
          let secondsLeft = Number(cooldown.dataset.seconds || 0);
          const renderCooldown = () => {
            const hours = Math.floor(secondsLeft / 3600);
            const minutes = Math.floor((secondsLeft % 3600) / 60);
            const seconds = Math.floor(secondsLeft % 60);
            cooldown.textContent = `${hours} ч ${String(minutes).padStart(2, "0")} мин ${String(seconds).padStart(2, "0")} сек`;
            secondsLeft = Math.max(0, secondsLeft - 1);
          };
          renderCooldown();
          setInterval(renderCooldown, 1000);
        }
      </script>
    """


class Handler(BaseHTTPRequestHandler):
    server_version = "ZombieDefenseWeb/1.0"

    def do_GET(self) -> None:
        current_user = self.current_user()
        notice = self.query().get("notice", [""])[0]
        if self.path_only() == "/api/state":
            if not current_user:
                self.respond_json({"hasSession": False, "notice": "Сначала войдите."}, HTTPStatus.UNAUTHORIZED)
                return
            self.respond_json(state_payload(current_user))
            return
        if self.path_only() == "/api/rules":
            if not current_user:
                self.respond_json({"ok": False, "message": "Сначала войдите."}, HTTPStatus.UNAUTHORIZED)
                return
            ok, message = call_game_proc("info", [])
            self.respond_json({"ok": ok, "message": message})
            return
        if self.path_only() == "/logout":
            self.logout()
            return
        if not current_user:
            self.respond(login_page(notice))
            return
        if self.path_only() == "/game":
            session = active_session(int(current_user["player_id"])) or latest_session(int(current_user["player_id"]))
            if session:
                self.respond(game_page(current_user, int(session["session_id"]), notice))
            else:
                self.respond(home_page(current_user, notice))
            return
        self.respond(home_page(current_user, notice))

    def do_POST(self) -> None:
        current_user = self.current_user()
        form = self.form()
        path = self.path_only()
        try:
            if path == "/register":
                self.register(form)
            elif path == "/login":
                self.login(form)
            elif not current_user:
                self.redirect("/", "Сначала войдите.")
            elif path == "/start":
                self.start_game(current_user, form)
            elif path == "/treat":
                self.treat(current_user, form)
            elif path == "/api/tick":
                self.api_tick(current_user, form)
            elif path == "/api/build":
                self.api_build(current_user, form)
            elif path == "/api/upgrade":
                self.api_upgrade(current_user, form)
            elif path == "/api/demolish":
                self.api_demolish(current_user, form)
            elif path == "/api/surrender":
                self.api_surrender(current_user)
            elif path == "/tick":
                self.tick(current_user, form)
            elif path == "/build":
                self.build(current_user, form)
            elif path == "/upgrade":
                self.upgrade(current_user, form)
            elif path == "/surrender":
                self.surrender(current_user)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.redirect("/", str(exc))

    def register(self, form: dict[str, list[str]]) -> None:
        player_name = form.get("player_name", [""])[0].strip()
        password = form.get("password", [""])[0]
        ok, message = call_game_proc("register_player", [player_name, password])
        if ok:
            self.create_session(find_player(player_name))
            self.redirect("/")
        else:
            self.redirect("/", message)

    def login(self, form: dict[str, list[str]]) -> None:
        player_name = form.get("player_name", [""])[0].strip()
        password = form.get("password", [""])[0]
        ok, message = call_game_proc("login_player", [player_name, password])
        if ok:
            self.create_session(find_player(player_name))
            self.redirect("/", "")
        else:
            self.redirect("/", message)

    def start_game(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        map_id = int(form.get("map_id", ["0"])[0])
        ok, message = call_game_proc("start_game", [int(current_user["player_id"]), map_id])
        self.redirect("/game", error_notice(ok, message))

    def treat(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        target_player_name = form.get("target_player_name", [""])[0].strip()
        action_type = form.get("action_type", [""])[0].strip()
        ok, message = call_game_proc(
            "trick_or_treat",
            [int(current_user["player_id"]), target_player_name, action_type],
        )
        if ok:
            action = latest_treat_action(int(current_user["player_id"]), target_player_name, action_type)
            backfired = bool(action and int(action["backfired"]) == 1)
            self.redirect("/", treat_success_notice(target_player_name, action_type, backfired))
        else:
            self.redirect("/", error_notice(ok, message))

    def api_tick(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.respond_json(state_payload(current_user, notice="Активная партия не найдена."))
            return
        session_id = int(session["session_id"])
        amount = max(1, min(10, int(form.get("amount", ["1"])[0])))
        delta_time = max(0.01, min(1.0, float(form.get("delta", ["1"])[0])))
        message = ""
        shots: list[dict[str, Any]] = []
        rewards: list[dict[str, Any]] = []
        for _ in range(amount):
            before_session = latest_session(int(current_user["player_id"])) or session
            before_zombies = active_zombie_rows(session_id)
            towers = tower_rows(session_id)
            ok, proc_message = call_game_proc("tick", [session_id, delta_time])
            message = error_notice(ok, proc_message)
            after_zombies = active_zombie_rows(session_id) if ok else before_zombies
            after_all_zombies = zombie_rows_by_ids(session_id, before_zombies) if ok else []
            shots = infer_shots(before_zombies, after_zombies, towers, delta_time)
            after_session = latest_session(int(current_user["player_id"])) or before_session
            killed_delta = int(after_session.get("killed_zombies", 0)) - int(before_session.get("killed_zombies", 0))
            money_delta = int(after_session.get("money", 0)) - int(before_session.get("money", 0))
            rewards = infer_rewards(after_all_zombies, killed_delta, money_delta)
            if not ok or not active_session(int(current_user["player_id"])):
                break
        self.respond_json(state_payload(current_user, shots=shots, rewards=rewards, notice=message))

    def api_build(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.respond_json(state_payload(current_user, notice="Активная партия не найдена."))
            return
        ok, message = call_game_proc(
            "place_tower",
            [
                int(session["session_id"]),
                int(form.get("tower_type_id", ["0"])[0]),
                int(form.get("build_point_id", ["0"])[0]),
            ],
        )
        self.respond_json(state_payload(current_user, notice=error_notice(ok, message)))

    def api_upgrade(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.respond_json(state_payload(current_user, notice="Активная партия не найдена."))
            return
        ok, message = call_game_proc(
            "upgrade_tower",
            [int(session["session_id"]), int(form.get("placed_tower_id", ["0"])[0])],
        )
        self.respond_json(state_payload(current_user, notice=error_notice(ok, message)))

    def api_demolish(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.respond_json(state_payload(current_user, notice="Активная партия не найдена."))
            return
        ok, message = call_game_proc(
            "demolish_tower",
            [int(session["session_id"]), int(form.get("placed_tower_id", ["0"])[0])],
        )
        self.respond_json(state_payload(current_user, notice=error_notice(ok, message)))

    def api_surrender(self, current_user: dict[str, Any]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.respond_json(state_payload(current_user, notice="Активная партия не найдена."))
            return
        ok, message = call_game_proc("end_game", [int(session["session_id"]), "lose"])
        self.respond_json(state_payload(current_user, notice=error_notice(ok, message)))

    def tick(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.redirect("/", "Активная партия не найдена.")
            return
        amount = max(1, min(25, int(form.get("amount", ["1"])[0])))
        message = ""
        for _ in range(amount):
            ok, proc_message = call_game_proc("tick", [int(session["session_id"]), 1])
            message = error_notice(ok, proc_message)
            if not ok or not active_session(int(current_user["player_id"])):
                break
        self.redirect("/game", message)

    def build(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.redirect("/", "Активная партия не найдена.")
            return
        ok, message = call_game_proc(
            "place_tower",
            [
                int(session["session_id"]),
                int(form.get("tower_type_id", ["0"])[0]),
                int(form.get("build_point_id", ["0"])[0]),
            ],
        )
        self.redirect("/game", error_notice(ok, message))

    def upgrade(self, current_user: dict[str, Any], form: dict[str, list[str]]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.redirect("/", "Активная партия не найдена.")
            return
        ok, message = call_game_proc(
            "upgrade_tower",
            [int(session["session_id"]), int(form.get("placed_tower_id", ["0"])[0])],
        )
        self.redirect("/game", error_notice(ok, message))

    def surrender(self, current_user: dict[str, Any]) -> None:
        session = active_session(int(current_user["player_id"]))
        if not session:
            self.redirect("/", "Активная партия не найдена.")
            return
        ok, message = call_game_proc("end_game", [int(session["session_id"]), "lose"])
        self.redirect("/game", error_notice(ok, message))

    def current_user(self) -> dict[str, Any] | None:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get("zd_session")
        if not token:
            return None
        return SESSIONS.get(token.value)

    def create_session(self, player: dict[str, Any] | None) -> None:
        if not player:
            return
        token = secrets.token_urlsafe(24)
        SESSIONS[token] = {"player_id": int(player["player_id"]), "player_name": str(player["player_name"])}
        self.pending_cookie = f"zd_session={token}; HttpOnly; SameSite=Lax; Path=/"

    def logout(self) -> None:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get("zd_session")
        if token:
            SESSIONS.pop(token.value, None)
        self.pending_cookie = "zd_session=; Max-Age=0; Path=/"
        self.redirect("/")

    def query(self) -> dict[str, list[str]]:
        parts = self.path.split("?", 1)
        return parse_qs(parts[1]) if len(parts) == 2 else {}

    def path_only(self) -> str:
        return self.path.split("?", 1)[0]

    def form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(data)

    def redirect(self, path: str, notice: str = "") -> None:
        if notice:
            notice = repair_mojibake(notice)
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}{urlencode({'notice': notice})}"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        if hasattr(self, "pending_cookie"):
            self.send_header("Set-Cookie", self.pending_cookie)
        self.end_headers()

    def respond(self, content: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        if hasattr(self, "pending_cookie"):
            self.send_header("Set-Cookie", self.pending_cookie)
        self.end_headers()
        self.wfile.write(content)

    def respond_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(json_ready(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(content)))
        if hasattr(self, "pending_cookie"):
            self.send_header("Set-Cookie", self.pending_cookie)
        self.end_headers()
        self.wfile.write(content)


def main() -> int:
    try:
        with connect() as connection:
            pass
    except Exception as exc:
        print(f"Не удалось подключиться к Oracle: {exc}", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Zombie Defense Web: http://{HOST}:{PORT}")
    print(f"Oracle: {DB_USER}@{DB_DSN}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
