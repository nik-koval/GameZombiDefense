CREATE OR REPLACE PACKAGE zombie_defense AS
  PROCEDURE register_player(p_player_name IN VARCHAR2, p_player_password IN VARCHAR2);
  PROCEDURE login_player(p_player_name IN VARCHAR2, p_player_password IN VARCHAR2);
  PROCEDURE info;
  PROCEDURE trick_or_treat(p_player_id IN INTEGER, p_target_player_name IN VARCHAR2, p_action_type IN VARCHAR2);
  PROCEDURE start_game(p_player_id IN INTEGER, p_map_id IN INTEGER);
  PROCEDURE place_tower(p_session_id IN INTEGER, p_tower_type_id IN INTEGER, p_build_point_id IN INTEGER);
  PROCEDURE upgrade_tower(p_session_id IN INTEGER, p_placed_tower_id IN INTEGER);
  PROCEDURE demolish_tower(p_session_id IN INTEGER, p_placed_tower_id IN INTEGER);
  PROCEDURE tick(p_session_id IN INTEGER, p_delta_time IN NUMBER);
  PROCEDURE spawn_zombies(p_session_id IN INTEGER);
  PROCEDURE move_zombies(p_session_id IN INTEGER, p_delta_time IN NUMBER);
  FUNCTION in_range(p_session_id IN INTEGER, p_placed_tower_id IN INTEGER, p_active_zombie_id IN INTEGER)
    RETURN NUMBER;
  PROCEDURE towers_shoot(p_session_id IN INTEGER, p_delta_time IN NUMBER);
  FUNCTION deal_damage(p_session_id IN INTEGER, p_active_zombie_id IN INTEGER, p_damage IN INTEGER, p_tower_code IN VARCHAR2)
    RETURN INTEGER;
  PROCEDURE check_wave_end(p_session_id IN INTEGER);
  PROCEDURE check_game_over(p_session_id IN INTEGER);
  PROCEDURE end_game(p_session_id IN INTEGER, p_result_status IN VARCHAR2);
  PROCEDURE show_history(p_player_id IN INTEGER);
END zombie_defense;
/

CREATE OR REPLACE PACKAGE BODY zombie_defense AS

  FUNCTION password_hash(p_password IN VARCHAR2) RETURN VARCHAR2 IS
  BEGIN
    RETURN TO_CHAR(DBMS_UTILITY.GET_HASH_VALUE(p_password, 1, 2147483647));
  END password_hash;

  FUNCTION max_wave_for_map(p_map_id IN INTEGER) RETURN INTEGER IS
    v_max_wave INTEGER;
  BEGIN
    SELECT MAX(wave_number)
    INTO v_max_wave
    FROM waves
    WHERE map_id = p_map_id;

    IF v_max_wave IS NULL THEN
      RAISE_APPLICATION_ERROR(-20005, 'Для выбранной карты не найдены волны.');
    END IF;

    RETURN v_max_wave;
  END max_wave_for_map;

  FUNCTION player_money_bonus(p_player_id IN INTEGER) RETURN NUMBER IS
    v_bonus NUMBER;
  BEGIN
    SELECT NVL(SUM(effect_value), 0)
    INTO v_bonus
    FROM player_effects
    WHERE target_player_id = p_player_id
      AND effect_type = 'MONEY_BONUS'
      AND starts_at <= SYSDATE
      AND expires_at > SYSDATE;

    RETURN v_bonus;
  END player_money_bonus;

  FUNCTION player_zombie_speed_multiplier(p_player_id IN INTEGER) RETURN NUMBER IS
    v_bonus NUMBER;
  BEGIN
    SELECT NVL(SUM(effect_value), 0)
    INTO v_bonus
    FROM player_effects
    WHERE target_player_id = p_player_id
      AND effect_type = 'ZOMBIE_SPEED'
      AND starts_at <= SYSDATE
      AND expires_at > SYSDATE;

    RETURN 1 + v_bonus;
  END player_zombie_speed_multiplier;

  PROCEDURE ensure_running(p_session_id IN NUMBER) IS
    v_status game_sessions.status%TYPE;
  BEGIN
    SELECT status
    INTO v_status
    FROM game_sessions
    WHERE session_id = p_session_id;

    IF v_status != 'running' THEN
      RAISE_APPLICATION_ERROR(-20001, 'Игровая сессия уже завершена.');
    END IF;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20002, 'Игровая сессия не найдена.');
  END ensure_running;

  PROCEDURE init_wave(p_session_id IN NUMBER, p_wave_number IN NUMBER) IS
    v_map_id game_sessions.map_id%TYPE;
    v_wave_id waves.wave_id%TYPE;
  BEGIN
    SELECT map_id
    INTO v_map_id
    FROM game_sessions
    WHERE session_id = p_session_id;

    SELECT wave_id
    INTO v_wave_id
    FROM waves
    WHERE map_id = v_map_id
      AND wave_number = p_wave_number;

    INSERT INTO wave_progress(session_id, wave_id, wave_item_id, released_count, next_spawn_time)
    SELECT
      p_session_id,
      w.wave_id,
      wc.wave_item_id,
      0,
      gs.simulation_time + wc.initial_delay
    FROM waves w
    JOIN wave_composition wc ON wc.wave_id = w.wave_id
    JOIN game_sessions gs ON gs.session_id = p_session_id
    WHERE w.wave_id = v_wave_id;

    IF SQL%ROWCOUNT = 0 THEN
      RAISE_APPLICATION_ERROR(-20003, 'У выбранной волны нет состава.');
    END IF;

    DBMS_OUTPUT.PUT_LINE('Подготовлена волна #' || p_wave_number || '.');
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20004, 'Волна #' || p_wave_number || ' для выбранной карты не найдена.');
  END init_wave;

  PROCEDURE get_zombie_position(
    p_active_zombie_id IN NUMBER,
    p_x OUT NUMBER,
    p_y OUT NUMBER,
    p_point_order OUT NUMBER
  ) IS
    v_map_id map_route.map_id%TYPE;
    v_x1 map_route.x%TYPE;
    v_y1 map_route.y%TYPE;
    v_x2 map_route.x%TYPE;
    v_y2 map_route.y%TYPE;
    v_progress active_zombies.segment_progress%TYPE;
  BEGIN
    SELECT mr.map_id, mr.x, mr.y, mr.point_order, az.segment_progress
    INTO v_map_id, v_x1, v_y1, p_point_order, v_progress
    FROM active_zombies az
    JOIN map_route mr ON mr.route_point_id = az.route_point_id
    WHERE az.active_zombie_id = p_active_zombie_id;

    BEGIN
      SELECT x, y
      INTO v_x2, v_y2
      FROM map_route
      WHERE map_id = v_map_id
        AND point_order = p_point_order + 1;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        v_x2 := v_x1;
        v_y2 := v_y1;
    END;

    p_x := v_x1 + (v_x2 - v_x1) * v_progress;
    p_y := v_y1 + (v_y2 - v_y1) * v_progress;
  END get_zombie_position;

  FUNCTION zombie_distance(p_first_zombie_id IN NUMBER, p_second_zombie_id IN NUMBER) RETURN NUMBER IS
    v_x1 NUMBER;
    v_y1 NUMBER;
    v_o1 NUMBER;
    v_x2 NUMBER;
    v_y2 NUMBER;
    v_o2 NUMBER;
  BEGIN
    get_zombie_position(p_first_zombie_id, v_x1, v_y1, v_o1);
    get_zombie_position(p_second_zombie_id, v_x2, v_y2, v_o2);
    RETURN SQRT(POWER(v_x1 - v_x2, 2) + POWER(v_y1 - v_y2, 2));
  END zombie_distance;

  PROCEDURE register_player(p_player_name IN VARCHAR2, p_player_password IN VARCHAR2) IS
    v_count NUMBER;
    v_password_hash players.password_hash%TYPE;
  BEGIN
    IF TRIM(p_player_name) IS NULL THEN
      RAISE_APPLICATION_ERROR(-20010, 'Имя игрока не может быть пустым.');
    END IF;

    IF TRIM(p_player_password) IS NULL THEN
      RAISE_APPLICATION_ERROR(-20011, 'Пароль не может быть пустым.');
    END IF;

    SELECT COUNT(*)
    INTO v_count
    FROM players
    WHERE LOWER(player_name) = LOWER(TRIM(p_player_name));

    IF v_count > 0 THEN
      RAISE_APPLICATION_ERROR(-20012, 'Игрок с таким именем уже существует.');
    END IF;

    v_password_hash := password_hash(p_player_password);

    INSERT INTO players(player_name, password_hash, registered_at)
    VALUES (TRIM(p_player_name), v_password_hash, SYSDATE);

    DBMS_OUTPUT.PUT_LINE('Игрок "' || TRIM(p_player_name) || '" зарегистрирован.');
  END register_player;

  PROCEDURE login_player(p_player_name IN VARCHAR2, p_player_password IN VARCHAR2) IS
    v_player_id players.player_id%TYPE;
    v_expected_hash players.password_hash%TYPE;
  BEGIN
    SELECT player_id, password_hash
    INTO v_player_id, v_expected_hash
    FROM players
    WHERE LOWER(player_name) = LOWER(TRIM(p_player_name));

    IF v_expected_hash != password_hash(p_player_password) THEN
      RAISE_APPLICATION_ERROR(-20013, 'Неверный пароль.');
    END IF;

    DBMS_OUTPUT.PUT_LINE('Вход выполнен. ID игрока: ' || v_player_id || '.');
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20014, 'Игрок не найден.');
  END login_player;

  PROCEDURE info IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE('Добро пожаловать в игру "Зомби-защита"!');
    DBMS_OUTPUT.PUT_LINE('register_player - создать игрока, login_player - войти.');
    DBMS_OUTPUT.PUT_LINE('start_game - начать партию на выбранной карте.');
    DBMS_OUTPUT.PUT_LINE('place_tower - установить башню, upgrade_tower - улучшить ее.');
    DBMS_OUTPUT.PUT_LINE('tick - продвинуть симуляцию: спавн, движение, атаки и проверка финала.');
    DBMS_OUTPUT.PUT_LINE('show_history - вывести историю завершенных партий.');
  END info;

  PROCEDURE trick_or_treat(p_player_id IN INTEGER, p_target_player_name IN VARCHAR2, p_action_type IN VARCHAR2) IS
    v_actor_count NUMBER;
    v_target_id players.player_id%TYPE;
    v_target_name players.player_name%TYPE;
    v_action_type VARCHAR2(20);
    v_effect_type VARCHAR2(30);
    v_effect_value NUMBER(8,2);
    v_today_count NUMBER;
    v_action_id trick_or_treat_actions.action_id%TYPE;
    v_backfired NUMBER := 0;
  BEGIN
    SELECT COUNT(*)
    INTO v_actor_count
    FROM players
    WHERE player_id = p_player_id;

    IF v_actor_count = 0 THEN
      RAISE_APPLICATION_ERROR(-20200, 'Игрок, выполняющий действие, не найден.');
    END IF;

    IF TRIM(p_target_player_name) IS NULL THEN
      RAISE_APPLICATION_ERROR(-20201, 'Введите имя игрока, на которого применяется эффект.');
    END IF;

    SELECT player_id, player_name
    INTO v_target_id, v_target_name
    FROM players
    WHERE LOWER(player_name) = LOWER(TRIM(p_target_player_name));

    IF v_target_id = p_player_id THEN
      RAISE_APPLICATION_ERROR(-20202, 'Нельзя выбрать самого себя.');
    END IF;

    v_action_type := UPPER(TRIM(p_action_type));
    IF v_action_type IS NULL OR v_action_type NOT IN ('SWEET', 'TRICK') THEN
      RAISE_APPLICATION_ERROR(-20204, 'Выберите действие SWEET или TRICK.');
    END IF;

    SELECT COUNT(*)
    INTO v_today_count
    FROM trick_or_treat_actions
    WHERE player_id = p_player_id
      AND action_type = v_action_type
      AND created_at >= TRUNC(SYSDATE);

    IF v_today_count > 0 THEN
      IF v_action_type = 'SWEET' THEN
        RAISE_APPLICATION_ERROR(-20203, 'Сегодня сладость уже отправлена. Следующая сладость будет доступна завтра.');
      ELSE
        RAISE_APPLICATION_ERROR(-20203, 'Сегодня гадость уже отправлена. Следующая гадость будет доступна завтра.');
      END IF;
    END IF;

    IF v_action_type = 'SWEET' THEN
      v_effect_type := 'MONEY_BONUS';
      v_effect_value := 100;
    ELSE
      v_effect_type := 'ZOMBIE_SPEED';
      v_effect_value := 0.15;
      IF DBMS_RANDOM.VALUE(0, 1) < 0.33 THEN
        v_backfired := 1;
      END IF;
    END IF;

    INSERT INTO trick_or_treat_actions(player_id, target_player_id, action_type, backfired, created_at)
    VALUES (p_player_id, v_target_id, v_action_type, v_backfired, SYSDATE)
    RETURNING action_id INTO v_action_id;

    INSERT INTO player_effects(
      action_id,
      source_player_id,
      target_player_id,
      effect_type,
      effect_value,
      starts_at,
      expires_at,
      is_backfire
    )
    VALUES (
      v_action_id,
      p_player_id,
      v_target_id,
      v_effect_type,
      v_effect_value,
      SYSDATE,
      SYSDATE + 12 / 24,
      0
    );

    IF v_backfired = 1 THEN
      INSERT INTO player_effects(
        action_id,
        source_player_id,
        target_player_id,
        effect_type,
        effect_value,
        starts_at,
        expires_at,
        is_backfire
      )
      VALUES (
        v_action_id,
        p_player_id,
        p_player_id,
        v_effect_type,
        v_effect_value,
        SYSDATE,
        SYSDATE + 12 / 24,
        1
      );
    END IF;

    IF v_action_type = 'SWEET' THEN
      UPDATE game_sessions
      SET money = money + v_effect_value
      WHERE player_id = v_target_id
        AND status = 'running';

      DBMS_OUTPUT.PUT_LINE('Сладость отправлена игроку "' || v_target_name || '": +100 стартовых монет на 12 часов.');
    ELSIF v_backfired = 1 THEN
      DBMS_OUTPUT.PUT_LINE('Гадость отправлена игроку "' || v_target_name || '": зомби быстрее на 15% на 12 часов. Эффект сработал и на вас.');
    ELSE
      DBMS_OUTPUT.PUT_LINE('Гадость отправлена игроку "' || v_target_name || '": зомби быстрее на 15% на 12 часов.');
    END IF;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20205, 'Игрок с таким именем не найден.');
    WHEN DUP_VAL_ON_INDEX THEN
      RAISE_APPLICATION_ERROR(-20206, 'Сегодня выбор уже сделан. Следующая попытка будет доступна завтра.');
  END trick_or_treat;

  PROCEDURE start_game(p_player_id IN INTEGER, p_map_id IN INTEGER) IS
    v_player_name players.player_name%TYPE;
    v_map_name maps.map_name%TYPE;
    v_start_money maps.start_money%TYPE;
    v_start_health maps.start_base_health%TYPE;
    v_money_bonus NUMBER;
    v_session_id game_sessions.session_id%TYPE;
  BEGIN
    SELECT player_name
    INTO v_player_name
    FROM players
    WHERE player_id = p_player_id;

    SELECT map_name, start_money, start_base_health
    INTO v_map_name, v_start_money, v_start_health
    FROM maps
    WHERE map_id = p_map_id;

    v_money_bonus := player_money_bonus(p_player_id);
    v_start_money := v_start_money + v_money_bonus;

    BEGIN
      SELECT session_id
      INTO v_session_id
      FROM game_sessions
      WHERE player_id = p_player_id
        AND status = 'running'
        AND ROWNUM = 1;

      DBMS_OUTPUT.PUT_LINE('У игрока уже есть активная сессия #' || v_session_id || '.');
      RETURN;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        NULL;
    END;

    INSERT INTO game_sessions(
      player_id,
      player_name,
      map_id,
      money,
      base_health,
      current_wave,
      killed_zombies,
      simulation_time,
      status,
      created_at
    )
    VALUES (
      p_player_id,
      v_player_name,
      p_map_id,
      v_start_money,
      v_start_health,
      1,
      0,
      0,
      'running',
      SYSDATE
    )
    RETURNING session_id INTO v_session_id;

    init_wave(v_session_id, 1);
    DBMS_OUTPUT.PUT_LINE('Стартовала сессия #' || v_session_id || ' на карте "' || v_map_name || '".');
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20020, 'Игрок или карта не найдены.');
  END start_game;

  PROCEDURE place_tower(p_session_id IN INTEGER, p_tower_type_id IN INTEGER, p_build_point_id IN INTEGER) IS
    v_map_id game_sessions.map_id%TYPE;
    v_money game_sessions.money%TYPE;
    v_tower_cost tower_types.cost%TYPE;
    v_level tower_types.level_no%TYPE;
    v_point_map build_points.map_id%TYPE;
    v_busy_count NUMBER;
    v_tower_name tower_types.tower_name%TYPE;
    v_fire_rate tower_types.fire_rate%TYPE;
  BEGIN
    ensure_running(p_session_id);

    SELECT map_id, money
    INTO v_map_id, v_money
    FROM game_sessions
    WHERE session_id = p_session_id;

    SELECT cost, level_no, tower_name, fire_rate
    INTO v_tower_cost, v_level, v_tower_name, v_fire_rate
    FROM tower_types
    WHERE tower_type_id = p_tower_type_id;

    IF v_level != 1 THEN
      RAISE_APPLICATION_ERROR(-20030, 'Строить можно только башню первого уровня.');
    END IF;

    SELECT map_id
    INTO v_point_map
    FROM build_points
    WHERE build_point_id = p_build_point_id;

    IF v_point_map != v_map_id THEN
      RAISE_APPLICATION_ERROR(-20031, 'Точка постройки принадлежит другой карте.');
    END IF;

    SELECT COUNT(*)
    INTO v_busy_count
    FROM placed_towers
    WHERE session_id = p_session_id
      AND build_point_id = p_build_point_id;

    IF v_busy_count > 0 THEN
      RAISE_APPLICATION_ERROR(-20032, 'Точка постройки уже занята.');
    END IF;

    IF v_money < v_tower_cost THEN
      RAISE_APPLICATION_ERROR(-20033, 'Недостаточно денег для покупки башни.');
    END IF;

    UPDATE game_sessions
    SET money = money - v_tower_cost
    WHERE session_id = p_session_id;

    INSERT INTO placed_towers(session_id, tower_type_id, remaining_cooldown, build_point_id)
    VALUES (p_session_id, p_tower_type_id, v_fire_rate, p_build_point_id);

    DBMS_OUTPUT.PUT_LINE('Построена башня "' || v_tower_name || '".');
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20034, 'Башня или точка постройки не найдена.');
  END place_tower;

  PROCEDURE upgrade_tower(p_session_id IN INTEGER, p_placed_tower_id IN INTEGER) IS
    v_current_type tower_types.tower_type_id%TYPE;
    v_code tower_types.tower_code%TYPE;
    v_level tower_types.level_no%TYPE;
    v_next_type tower_types.tower_type_id%TYPE;
    v_next_cost tower_types.cost%TYPE;
    v_next_name tower_types.tower_name%TYPE;
    v_money game_sessions.money%TYPE;
  BEGIN
    ensure_running(p_session_id);

    SELECT pt.tower_type_id, tt.tower_code, tt.level_no
    INTO v_current_type, v_code, v_level
    FROM placed_towers pt
    JOIN tower_types tt ON tt.tower_type_id = pt.tower_type_id
    WHERE pt.session_id = p_session_id
      AND pt.placed_tower_id = p_placed_tower_id;

    IF v_level >= 3 THEN
      RAISE_APPLICATION_ERROR(-20040, 'Башня уже имеет максимальный уровень.');
    END IF;

    SELECT tower_type_id, cost, tower_name
    INTO v_next_type, v_next_cost, v_next_name
    FROM tower_types
    WHERE tower_code = v_code
      AND level_no = v_level + 1;

    SELECT money
    INTO v_money
    FROM game_sessions
    WHERE session_id = p_session_id;

    IF v_money < v_next_cost THEN
      RAISE_APPLICATION_ERROR(-20041, 'Недостаточно денег для улучшения.');
    END IF;

    UPDATE game_sessions
    SET money = money - v_next_cost
    WHERE session_id = p_session_id;

    UPDATE placed_towers
    SET tower_type_id = v_next_type
    WHERE placed_tower_id = p_placed_tower_id
      AND session_id = p_session_id;

    DBMS_OUTPUT.PUT_LINE('Башня улучшена до "' || v_next_name || '".');
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20042, 'Установленная башня или следующий уровень не найдены.');
  END upgrade_tower;

  PROCEDURE demolish_tower(p_session_id IN INTEGER, p_placed_tower_id IN INTEGER) IS
    v_code tower_types.tower_code%TYPE;
    v_level tower_types.level_no%TYPE;
    v_spent NUMBER;
    v_refund NUMBER;
  BEGIN
    ensure_running(p_session_id);

    SELECT tt.tower_code, tt.level_no
    INTO v_code, v_level
    FROM placed_towers pt
    JOIN tower_types tt ON tt.tower_type_id = pt.tower_type_id
    WHERE pt.session_id = p_session_id
      AND pt.placed_tower_id = p_placed_tower_id
    FOR UPDATE;

    SELECT SUM(cost)
    INTO v_spent
    FROM tower_types
    WHERE tower_code = v_code
      AND level_no <= v_level;

    v_refund := ROUND(NVL(v_spent, 0) * 0.5);

    DELETE FROM placed_towers
    WHERE session_id = p_session_id
      AND placed_tower_id = p_placed_tower_id;

    UPDATE game_sessions
    SET money = money + v_refund
    WHERE session_id = p_session_id;

    DBMS_OUTPUT.PUT_LINE('Башня снесена. Возвращено монет: ' || v_refund || '.');
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20043, 'Установленная башня не найдена.');
  END demolish_tower;

  PROCEDURE spawn_zombies(p_session_id IN INTEGER) IS
    v_map_id game_sessions.map_id%TYPE;
    v_start_route_id map_route.route_point_id%TYPE;
    v_start_x map_route.x%TYPE;
    v_start_y map_route.y%TYPE;
    v_next_x map_route.x%TYPE;
    v_next_y map_route.y%TYPE;
    v_segment_len NUMBER;
    v_initial_progress NUMBER;
    v_armor_multiplier NUMBER;
    v_spawn_health NUMBER;
    v_speed_multiplier NUMBER;
  BEGIN
    ensure_running(p_session_id);

    SELECT map_id
    INTO v_map_id
    FROM game_sessions
    WHERE session_id = p_session_id;

    SELECT route_point_id, x, y
    INTO v_start_route_id, v_start_x, v_start_y
    FROM map_route
    WHERE map_id = v_map_id
      AND point_order = 1;

    BEGIN
      SELECT x, y
      INTO v_next_x, v_next_y
      FROM map_route
      WHERE map_id = v_map_id
        AND point_order = 2;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        v_next_x := v_start_x;
        v_next_y := v_start_y;
    END;

    v_segment_len := SQRT(POWER(v_next_x - v_start_x, 2) + POWER(v_next_y - v_start_y, 2));
    IF v_segment_len = 0 THEN
      v_segment_len := 1;
    END IF;

    FOR item IN (
      SELECT
        wp.wave_progress_id,
        wp.released_count,
        wp.next_spawn_time,
        wc.zombie_count,
        wc.spawn_interval,
        wc.zombie_type_id,
        zt.base_health,
        zt.base_speed,
        w.wave_number,
        gs.player_id,
        gs.simulation_time
      FROM wave_progress wp
      JOIN wave_composition wc ON wc.wave_item_id = wp.wave_item_id
      JOIN waves w ON w.wave_id = wp.wave_id
      JOIN zombie_types zt ON zt.zombie_type_id = wc.zombie_type_id
      JOIN game_sessions gs ON gs.session_id = wp.session_id
      WHERE wp.session_id = p_session_id
        AND wp.released_count < wc.zombie_count
        AND wp.next_spawn_time <= gs.simulation_time
    ) LOOP
      v_speed_multiplier := player_zombie_speed_multiplier(item.player_id);
      v_initial_progress := LEAST(
        GREATEST((item.simulation_time - item.next_spawn_time) * item.base_speed * v_speed_multiplier / v_segment_len, 0),
        0.95
      );
      v_armor_multiplier := ROUND(1 + ((item.wave_number - 1) * 0.14), 2);
      v_spawn_health := ROUND(item.base_health * v_armor_multiplier);

      INSERT INTO active_zombies(
        wave_progress_id,
        zombie_type_id,
        current_health,
        max_health,
        armor_multiplier,
        route_point_id,
        segment_progress,
        status,
        slow_until,
        speed_multiplier
      )
      VALUES (
        item.wave_progress_id,
        item.zombie_type_id,
        v_spawn_health,
        v_spawn_health,
        v_armor_multiplier,
        v_start_route_id,
        v_initial_progress,
        'active',
        0,
        1
      );

      UPDATE wave_progress
      SET released_count = released_count + 1,
          next_spawn_time = next_spawn_time + item.spawn_interval
      WHERE wave_progress_id = item.wave_progress_id;
    END LOOP;
  END spawn_zombies;

  PROCEDURE move_zombies(p_session_id IN INTEGER, p_delta_time IN NUMBER) IS
    v_simulation_time game_sessions.simulation_time%TYPE;
    v_next_route_id map_route.route_point_id%TYPE;
    v_next_x map_route.x%TYPE;
    v_next_y map_route.y%TYPE;
    v_has_after NUMBER;
    v_speed NUMBER;
    v_distance_left NUMBER;
    v_distance_to_next NUMBER;
    v_segment_len NUMBER;
    v_progress NUMBER;
    v_current_route_id map_route.route_point_id%TYPE;
    v_current_order map_route.point_order%TYPE;
    v_current_x map_route.x%TYPE;
    v_current_y map_route.y%TYPE;
    v_arrived NUMBER;
    v_finish_progress NUMBER;
  BEGIN
    ensure_running(p_session_id);

    IF p_delta_time <= 0 THEN
      RAISE_APPLICATION_ERROR(-20050, 'Шаг времени должен быть положительным.');
    END IF;

    SELECT simulation_time
    INTO v_simulation_time
    FROM game_sessions
    WHERE session_id = p_session_id;

    FOR z IN (
      SELECT
        az.active_zombie_id,
        az.segment_progress,
        zt.base_speed,
        gs.player_id,
        mr.map_id,
        mr.route_point_id,
        mr.point_order,
        mr.x,
        mr.y
      FROM active_zombies az
      JOIN zombie_types zt ON zt.zombie_type_id = az.zombie_type_id
      JOIN map_route mr ON mr.route_point_id = az.route_point_id
      JOIN wave_progress wp ON wp.wave_progress_id = az.wave_progress_id
      JOIN game_sessions gs ON gs.session_id = wp.session_id
      WHERE wp.session_id = p_session_id
        AND az.status = 'active'
      ORDER BY az.active_zombie_id
    ) LOOP
      v_speed := z.base_speed * player_zombie_speed_multiplier(z.player_id);
      v_distance_left := v_speed * p_delta_time;
      v_progress := z.segment_progress;
      v_current_route_id := z.route_point_id;
      v_current_order := z.point_order;
      v_current_x := z.x;
      v_current_y := z.y;
      v_arrived := 0;

      WHILE v_distance_left > 0 AND v_arrived = 0 LOOP
        BEGIN
          SELECT route_point_id, x, y
          INTO v_next_route_id, v_next_x, v_next_y
          FROM map_route
          WHERE map_id = z.map_id
            AND point_order = v_current_order + 1;
        EXCEPTION
          WHEN NO_DATA_FOUND THEN
            v_arrived := 1;
            EXIT;
        END;

        v_segment_len := SQRT(POWER(v_next_x - v_current_x, 2) + POWER(v_next_y - v_current_y, 2));
        IF v_segment_len = 0 THEN
          v_segment_len := 1;
        END IF;

        v_distance_to_next := (1 - v_progress) * v_segment_len;

        IF v_distance_left < v_distance_to_next THEN
          v_progress := v_progress + (v_distance_left / v_segment_len);
          v_distance_left := 0;

          SELECT COUNT(*)
          INTO v_has_after
          FROM map_route
          WHERE map_id = z.map_id
            AND point_order = v_current_order + 2;

          IF v_has_after = 0 THEN
            v_finish_progress := GREATEST(0, LEAST(0.94, 1 - 0.85 / v_segment_len));
            IF v_progress >= v_finish_progress THEN
              v_arrived := 1;
            END IF;
          ELSIF v_progress >= 0.99 THEN
            v_current_route_id := v_next_route_id;
            v_current_order := v_current_order + 1;
            v_current_x := v_next_x;
            v_current_y := v_next_y;
            v_progress := 0;
          END IF;
        ELSE
          v_distance_left := v_distance_left - v_distance_to_next;

          SELECT COUNT(*)
          INTO v_has_after
          FROM map_route
          WHERE map_id = z.map_id
            AND point_order = v_current_order + 2;

          IF v_has_after = 0 THEN
            v_arrived := 1;
          ELSE
            v_current_route_id := v_next_route_id;
            v_current_order := v_current_order + 1;
            v_current_x := v_next_x;
            v_current_y := v_next_y;
            v_progress := 0;
          END IF;
        END IF;
      END LOOP;

      IF v_arrived = 1 THEN
        UPDATE active_zombies
        SET status = 'inactive',
            current_health = 0,
            segment_progress = 0
        WHERE active_zombie_id = z.active_zombie_id;

        UPDATE game_sessions
        SET base_health = GREATEST(0, base_health - 1)
        WHERE session_id = p_session_id;
      ELSE
        UPDATE active_zombies
        SET route_point_id = v_current_route_id,
            segment_progress = LEAST(v_progress, 0.99)
        WHERE active_zombie_id = z.active_zombie_id;
      END IF;
    END LOOP;
  END move_zombies;

  FUNCTION in_range(p_session_id IN INTEGER, p_placed_tower_id IN INTEGER, p_active_zombie_id IN INTEGER)
    RETURN NUMBER IS
    v_tower_x build_points.x%TYPE;
    v_tower_y build_points.y%TYPE;
    v_range tower_types.range_cells%TYPE;
    v_zombie_x NUMBER;
    v_zombie_y NUMBER;
    v_order NUMBER;
    v_count NUMBER;
    v_distance NUMBER;
  BEGIN
    SELECT COUNT(*)
    INTO v_count
    FROM active_zombies az
    JOIN wave_progress wp ON wp.wave_progress_id = az.wave_progress_id
    WHERE az.active_zombie_id = p_active_zombie_id
      AND wp.session_id = p_session_id
      AND az.status = 'active';

    IF v_count = 0 THEN
      RETURN 0;
    END IF;

    SELECT bp.x, bp.y, tt.range_cells
    INTO v_tower_x, v_tower_y, v_range
    FROM placed_towers pt
    JOIN build_points bp ON bp.build_point_id = pt.build_point_id
    JOIN tower_types tt ON tt.tower_type_id = pt.tower_type_id
    WHERE pt.session_id = p_session_id
      AND pt.placed_tower_id = p_placed_tower_id;

    get_zombie_position(p_active_zombie_id, v_zombie_x, v_zombie_y, v_order);
    v_distance := SQRT(POWER(v_tower_x - v_zombie_x, 2) + POWER(v_tower_y - v_zombie_y, 2));

    IF v_distance <= v_range THEN
      RETURN 1;
    END IF;
    RETURN 0;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN 0;
  END in_range;

  FUNCTION damage_multiplier(p_tower_code IN VARCHAR2, p_active_zombie_id IN INTEGER) RETURN NUMBER IS
    v_base_health zombie_types.base_health%TYPE;
    v_base_speed zombie_types.base_speed%TYPE;
  BEGIN
    SELECT zt.base_health, zt.base_speed
    INTO v_base_health, v_base_speed
    FROM active_zombies az
    JOIN zombie_types zt ON zt.zombie_type_id = az.zombie_type_id
    WHERE az.active_zombie_id = p_active_zombie_id;

    IF v_base_health <= 30 THEN
      IF p_tower_code = 'SNIPER' THEN
        RETURN 0.25;
      ELSIF p_tower_code = 'SPLASH' THEN
        RETURN 1.50;
      ELSE
        RETURN 1.00;
      END IF;
    ELSIF v_base_health >= 80 THEN
      IF p_tower_code = 'SNIPER' THEN
        RETURN 1.50;
      ELSIF p_tower_code = 'SPLASH' THEN
        RETURN 0.25;
      ELSE
        RETURN 0.50;
      END IF;
    ELSE
      IF p_tower_code = 'GUN' THEN
        RETURN 1.50;
      ELSE
        RETURN 1.00;
      END IF;
    END IF;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN 1.00;
  END damage_multiplier;

  FUNCTION deal_damage(p_session_id IN INTEGER, p_active_zombie_id IN INTEGER, p_damage IN INTEGER, p_tower_code IN VARCHAR2)
    RETURN INTEGER IS
    v_health active_zombies.current_health%TYPE;
    v_reward zombie_types.reward%TYPE;
    v_new_health NUMBER;
    v_actual_damage NUMBER;
  BEGIN
    IF p_damage <= 0 THEN
      RAISE_APPLICATION_ERROR(-20060, 'Урон должен быть положительным.');
    END IF;

    SELECT az.current_health, zt.reward
    INTO v_health, v_reward
    FROM active_zombies az
    JOIN zombie_types zt ON zt.zombie_type_id = az.zombie_type_id
    JOIN wave_progress wp ON wp.wave_progress_id = az.wave_progress_id
    WHERE az.active_zombie_id = p_active_zombie_id
      AND az.status = 'active'
      AND wp.session_id = p_session_id
    FOR UPDATE;

    v_actual_damage := GREATEST(1, ROUND(p_damage * damage_multiplier(p_tower_code, p_active_zombie_id)));
    v_new_health := v_health - v_actual_damage;

    IF v_new_health <= 0 THEN
      UPDATE active_zombies
      SET current_health = 0,
          status = 'inactive'
      WHERE active_zombie_id = p_active_zombie_id;

      UPDATE game_sessions
      SET money = money + v_reward,
          killed_zombies = killed_zombies + 1
      WHERE session_id = p_session_id;

      RETURN 1;
    END IF;

    UPDATE active_zombies
    SET current_health = v_new_health
    WHERE active_zombie_id = p_active_zombie_id;

    RETURN 0;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RETURN 0;
  END deal_damage;

  PROCEDURE towers_shoot(p_session_id IN INTEGER, p_delta_time IN NUMBER) IS
    v_new_cooldown NUMBER;
    v_target_id active_zombies.active_zombie_id%TYPE;
    v_killed INTEGER;
    v_simulation_time game_sessions.simulation_time%TYPE;
  BEGIN
    ensure_running(p_session_id);

    IF p_delta_time <= 0 THEN
      RAISE_APPLICATION_ERROR(-20070, 'Шаг времени должен быть положительным.');
    END IF;

    SELECT simulation_time
    INTO v_simulation_time
    FROM game_sessions
    WHERE session_id = p_session_id;

    FOR t IN (
      SELECT
        pt.placed_tower_id,
        pt.remaining_cooldown,
        tt.damage,
        tt.tower_code,
        tt.fire_rate,
        tt.splash_radius
      FROM placed_towers pt
      JOIN tower_types tt ON tt.tower_type_id = pt.tower_type_id
      WHERE pt.session_id = p_session_id
      ORDER BY pt.placed_tower_id
    ) LOOP
      v_new_cooldown := GREATEST(0, t.remaining_cooldown - p_delta_time);

      UPDATE placed_towers
      SET remaining_cooldown = v_new_cooldown
      WHERE placed_tower_id = t.placed_tower_id;

      IF v_new_cooldown = 0 THEN
        v_target_id := NULL;

        FOR z IN (
          SELECT az.active_zombie_id
          FROM active_zombies az
          JOIN wave_progress wp ON wp.wave_progress_id = az.wave_progress_id
          JOIN map_route mr ON mr.route_point_id = az.route_point_id
          WHERE wp.session_id = p_session_id
            AND az.status = 'active'
          ORDER BY mr.point_order DESC, az.segment_progress DESC, az.active_zombie_id
        ) LOOP
          IF in_range(p_session_id, t.placed_tower_id, z.active_zombie_id) = 1 THEN
            v_target_id := z.active_zombie_id;
            EXIT;
          END IF;
        END LOOP;

        IF v_target_id IS NOT NULL THEN
          IF t.splash_radius > 0 THEN
            FOR splash_target IN (
              SELECT az.active_zombie_id
              FROM active_zombies az
              JOIN wave_progress wp ON wp.wave_progress_id = az.wave_progress_id
              WHERE wp.session_id = p_session_id
                AND az.status = 'active'
            ) LOOP
              IF zombie_distance(v_target_id, splash_target.active_zombie_id) <= t.splash_radius THEN
                v_killed := deal_damage(p_session_id, splash_target.active_zombie_id, t.damage, t.tower_code);
              END IF;
            END LOOP;
          ELSE
            v_killed := deal_damage(p_session_id, v_target_id, t.damage, t.tower_code);
          END IF;

          UPDATE placed_towers
          SET remaining_cooldown = t.fire_rate
          WHERE placed_tower_id = t.placed_tower_id;
        END IF;
      END IF;
    END LOOP;
  END towers_shoot;

  PROCEDURE check_wave_end(p_session_id IN INTEGER) IS
    v_map_id game_sessions.map_id%TYPE;
    v_current_wave game_sessions.current_wave%TYPE;
    v_wave_id waves.wave_id%TYPE;
    v_remaining_to_spawn NUMBER;
    v_active_zombies NUMBER;
    v_next_wave_count NUMBER;
    v_max_wave NUMBER;
  BEGIN
    ensure_running(p_session_id);

    SELECT map_id, current_wave
    INTO v_map_id, v_current_wave
    FROM game_sessions
    WHERE session_id = p_session_id;

    SELECT wave_id
    INTO v_wave_id
    FROM waves
    WHERE map_id = v_map_id
      AND wave_number = v_current_wave;

    SELECT COUNT(*)
    INTO v_remaining_to_spawn
    FROM wave_progress wp
    JOIN wave_composition wc ON wc.wave_item_id = wp.wave_item_id
    WHERE wp.session_id = p_session_id
      AND wp.wave_id = v_wave_id
      AND wp.released_count < wc.zombie_count;

    SELECT COUNT(*)
    INTO v_active_zombies
    FROM active_zombies az
    JOIN wave_progress wp ON wp.wave_progress_id = az.wave_progress_id
    WHERE wp.session_id = p_session_id
      AND wp.wave_id = v_wave_id
      AND az.status = 'active';

    IF v_remaining_to_spawn = 0 AND v_active_zombies = 0 THEN
      v_max_wave := max_wave_for_map(v_map_id);

      IF v_current_wave >= v_max_wave THEN
        end_game(p_session_id, 'win');
        RETURN;
      END IF;

      SELECT COUNT(*)
      INTO v_next_wave_count
      FROM waves
      WHERE map_id = v_map_id
        AND wave_number = v_current_wave + 1;

      IF v_next_wave_count > 0 THEN
        UPDATE game_sessions
        SET current_wave = current_wave + 1
        WHERE session_id = p_session_id;

        init_wave(p_session_id, v_current_wave + 1);
      ELSE
        end_game(p_session_id, 'win');
      END IF;
    END IF;
  END check_wave_end;

  PROCEDURE check_game_over(p_session_id IN INTEGER) IS
    v_status game_sessions.status%TYPE;
    v_base_health game_sessions.base_health%TYPE;
  BEGIN
    SELECT status, base_health
    INTO v_status, v_base_health
    FROM game_sessions
    WHERE session_id = p_session_id;

    IF v_status != 'running' THEN
      RETURN;
    END IF;

    IF v_base_health <= 0 THEN
      end_game(p_session_id, 'lose');
    END IF;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20080, 'Игровая сессия не найдена.');
  END check_game_over;

  PROCEDURE tick(p_session_id IN INTEGER, p_delta_time IN NUMBER) IS
    v_status game_sessions.status%TYPE;
  BEGIN
    ensure_running(p_session_id);

    IF p_delta_time <= 0 THEN
      RAISE_APPLICATION_ERROR(-20090, 'Шаг времени должен быть положительным.');
    END IF;

    UPDATE game_sessions
    SET simulation_time = simulation_time + p_delta_time
    WHERE session_id = p_session_id;

    move_zombies(p_session_id, p_delta_time);
    check_game_over(p_session_id);

    SELECT status
    INTO v_status
    FROM game_sessions
    WHERE session_id = p_session_id;

    IF v_status != 'running' THEN
      RETURN;
    END IF;

    towers_shoot(p_session_id, p_delta_time);
    check_wave_end(p_session_id);
    check_game_over(p_session_id);

    SELECT status
    INTO v_status
    FROM game_sessions
    WHERE session_id = p_session_id;

    IF v_status = 'running' THEN
      spawn_zombies(p_session_id);
    END IF;
  END tick;

  PROCEDURE end_game(p_session_id IN INTEGER, p_result_status IN VARCHAR2) IS
    v_status game_sessions.status%TYPE;
    v_result_status VARCHAR2(10);
    v_session game_sessions%ROWTYPE;
  BEGIN
    v_result_status := LOWER(TRIM(p_result_status));
    IF v_result_status IS NULL OR v_result_status NOT IN ('win', 'lose') THEN
      RAISE_APPLICATION_ERROR(-20100, 'Итог игры должен быть win или lose.');
    END IF;

    SELECT *
    INTO v_session
    FROM game_sessions
    WHERE session_id = p_session_id
    FOR UPDATE;

    v_status := v_session.status;
    IF v_status != 'running' THEN
      DBMS_OUTPUT.PUT_LINE('Сессия #' || p_session_id || ' уже завершена со статусом ' || v_status || '.');
      RETURN;
    END IF;

    INSERT INTO game_results(
      session_id,
      player_id,
      player_name,
      map_id,
      result_status,
      reached_wave,
      duration,
      finished_at
    )
    VALUES (
      v_session.session_id,
      v_session.player_id,
      v_session.player_name,
      v_session.map_id,
      v_result_status,
      v_session.current_wave,
      v_session.simulation_time,
      SYSDATE
    );

    UPDATE game_sessions
    SET status = v_result_status
    WHERE session_id = p_session_id;

    IF v_result_status = 'win' THEN
      DBMS_OUTPUT.PUT_LINE('Победа! Все волны пройдены.');
    ELSE
      DBMS_OUTPUT.PUT_LINE('Поражение. База разрушена или партия завершена игроком.');
    END IF;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20101, 'Игровая сессия не найдена.');
    WHEN DUP_VAL_ON_INDEX THEN
      DBMS_OUTPUT.PUT_LINE('Результат этой сессии уже сохранен.');
  END end_game;

  PROCEDURE show_history(p_player_id IN INTEGER) IS
    v_count NUMBER := 0;
  BEGIN
    SELECT COUNT(*)
    INTO v_count
    FROM players
    WHERE player_id = p_player_id;

    IF v_count = 0 THEN
      RAISE_APPLICATION_ERROR(-20110, 'Игрок не найден.');
    END IF;

    v_count := 0;
    FOR r IN (
      SELECT
        gr.result_status,
        gr.reached_wave,
        gr.duration,
        gr.finished_at,
        m.map_name
      FROM game_results gr
      JOIN maps m ON m.map_id = gr.map_id
      WHERE gr.player_id = p_player_id
      ORDER BY gr.finished_at DESC
    ) LOOP
      v_count := v_count + 1;
      DBMS_OUTPUT.PUT_LINE(
        TO_CHAR(r.finished_at, 'YYYY-MM-DD HH24:MI') ||
        ' | ' || r.map_name ||
        ' | ' || r.result_status ||
        ' | волна ' || r.reached_wave ||
        ' | ' || r.duration || ' сек.'
      );
    END LOOP;

    IF v_count = 0 THEN
      DBMS_OUTPUT.PUT_LINE('История игрока пока пуста.');
    END IF;
  END show_history;

END zombie_defense;
/

SHOW ERRORS PACKAGE zombie_defense;
SHOW ERRORS PACKAGE BODY zombie_defense;
