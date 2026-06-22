INSERT INTO maps(map_name, map_width, map_height, start_money, start_base_health, difficulty)
VALUES (UNISTR('\0421\043A\043B\0430\0434 \0443 \0448\043E\0441\0441\0435'), 12, 7, 260, 10, 'easy');

INSERT INTO maps(map_name, map_width, map_height, start_money, start_base_health, difficulty)
VALUES (UNISTR('\041B\0435\0441\043D\0430\044F \0442\0440\043E\043F\0430'), 14, 9, 280, 9, 'normal');

INSERT INTO maps(map_name, map_width, map_height, start_money, start_base_health, difficulty)
VALUES (UNISTR('\041B\0430\0431\0438\0440\0438\043D\0442 \043B\0430\0431\043E\0440\0430\0442\043E\0440\0438\0438'), 16, 10, 320, 8, 'hard');

INSERT ALL
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 1, map_id, 1, 0, 3)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 2, map_id, 2, 11, 3)
SELECT map_id FROM maps WHERE map_name = UNISTR('\0421\043A\043B\0430\0434 \0443 \0448\043E\0441\0441\0435');

INSERT ALL
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 1 * 100 + 1, map_id, 1, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 2 * 100 + 5, map_id, 2, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 3 * 100 + 1, map_id, 3, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 4 * 100 + 5, map_id, 4, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 5 * 100 + 1, map_id, 5, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 6 * 100 + 5, map_id, 6, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 7 * 100 + 1, map_id, 7, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 8 * 100 + 5, map_id, 8, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 9 * 100 + 1, map_id, 9, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 10 * 100 + 5, map_id, 10, 4)
SELECT map_id FROM maps WHERE map_name = UNISTR('\0421\043A\043B\0430\0434 \0443 \0448\043E\0441\0441\0435');

INSERT ALL
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 1, map_id, 1, 0, 1)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 2, map_id, 2, 4, 1)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 3, map_id, 3, 4, 3)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 4, map_id, 4, 8, 3)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 5, map_id, 5, 8, 5)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 6, map_id, 6, 13, 5)
SELECT map_id FROM maps WHERE map_name = UNISTR('\041B\0435\0441\043D\0430\044F \0442\0440\043E\043F\0430');

INSERT ALL
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 2 * 100 + 0, map_id, 2, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 2 * 100 + 2, map_id, 3, 0)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 5 * 100 + 2, map_id, 6, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 6 * 100 + 4, map_id, 7, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 9 * 100 + 4, map_id, 9, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 10 * 100 + 6, map_id, 10, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 12 * 100 + 4, map_id, 12, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 3 * 100 + 4, map_id, 5, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 7 * 100 + 2, map_id, 7, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 1 * 100 + 2, map_id, 1, 0)
SELECT map_id FROM maps WHERE map_name = UNISTR('\041B\0435\0441\043D\0430\044F \0442\0440\043E\043F\0430');

INSERT ALL
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 1, map_id, 1, 0, 8)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 2, map_id, 2, 3, 8)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 3, map_id, 3, 3, 5)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 4, map_id, 4, 7, 5)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 5, map_id, 5, 7, 3)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 6, map_id, 6, 10, 3)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 7, map_id, 7, 10, 1)
  INTO map_route(route_point_id, map_id, point_order, x, y) VALUES (map_id * 100 + 8, map_id, 8, 15, 1)
SELECT map_id FROM maps WHERE map_name = UNISTR('\041B\0430\0431\0438\0440\0438\043D\0442 \043B\0430\0431\043E\0440\0430\0442\043E\0440\0438\0438');

INSERT ALL
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 1 * 100 + 7, map_id, 1, 7)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 2 * 100 + 6, map_id, 2, 7)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 4 * 100 + 6, map_id, 4, 7)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 5 * 100 + 4, map_id, 4, 6)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 6 * 100 + 6, map_id, 6, 4)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 8 * 100 + 2, map_id, 8, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 9 * 100 + 4, map_id, 6, 6)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 11 * 100 + 2, map_id, 9, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 12 * 100 + 0, map_id, 11, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 14 * 100 + 2, map_id, 13, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 5 * 100 + 7, map_id, 14, 2)
  INTO build_points(build_point_id, map_id, x, y) VALUES (map_id * 1000 + 9 * 100 + 1, map_id, 5, 4)
SELECT map_id FROM maps WHERE map_name = UNISTR('\041B\0430\0431\0438\0440\0438\043D\0442 \043B\0430\0431\043E\0440\0430\0442\043E\0440\0438\0438');

INSERT INTO zombie_types(zombie_type_name, base_health, base_speed, armor, reward)
VALUES (UNISTR('\041E\0431\044B\0447\043D\044B\0439'), 36, 0.50, 0, 12);
INSERT INTO zombie_types(zombie_type_name, base_health, base_speed, armor, reward)
VALUES (UNISTR('\0411\044B\0441\0442\0440\044B\0439'), 24, 0.90, 0, 10);
INSERT INTO zombie_types(zombie_type_name, base_health, base_speed, armor, reward)
VALUES (UNISTR('\0422\0430\043D\043A'), 95, 0.34, 0, 28);

INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('GUN', UNISTR('\041F\0443\043B\0435\043C\0451\0442') || ' I', 1, 14, 3, 1.20, 75, 0, 0, 0);
INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('GUN', UNISTR('\041F\0443\043B\0435\043C\0451\0442') || ' II', 2, 21, 3, 1.12, 115, 0, 0, 0);
INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('GUN', UNISTR('\041F\0443\043B\0435\043C\0451\0442') || ' III', 3, 29, 4, 1.05, 180, 0, 0, 0);

INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('SNIPER', UNISTR('\0421\043D\0430\0439\043F\0435\0440') || ' I', 1, 28, 4, 2.70, 120, 0, 0, 0);
INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('SNIPER', UNISTR('\0421\043D\0430\0439\043F\0435\0440') || ' II', 2, 45, 5, 2.50, 180, 0, 0, 0);
INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('SNIPER', UNISTR('\0421\043D\0430\0439\043F\0435\0440') || ' III', 3, 68, 5, 2.30, 270, 0, 0, 0);

INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('SPLASH', UNISTR('\041C\0438\043D\043E\043C\0451\0442') || ' I', 1, 9, 3, 2.45, 115, 2, 0, 0);
INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('SPLASH', UNISTR('\041C\0438\043D\043E\043C\0451\0442') || ' II', 2, 16, 3, 2.20, 170, 2, 0, 0);
INSERT INTO tower_types(tower_code, tower_name, level_no, damage, range_cells, fire_rate, cost, splash_radius, slow_percent, slow_duration)
VALUES ('SPLASH', UNISTR('\041C\0438\043D\043E\043C\0451\0442') || ' III', 3, 24, 4, 2.10, 260, 3, 0, 0);

DECLARE
  v_wave_count NUMBER;
BEGIN
  FOR map_rec IN (SELECT map_id, difficulty FROM maps ORDER BY map_id) LOOP
    v_wave_count := CASE map_rec.difficulty
      WHEN 'easy' THEN 5
      WHEN 'normal' THEN 6
      ELSE 7
    END;

    FOR wave_no IN 1 .. v_wave_count LOOP
      INSERT INTO waves(map_id, wave_number)
      VALUES (map_rec.map_id, wave_no);
    END LOOP;
  END LOOP;
END;
/

DECLARE
  v_normal_id zombie_types.zombie_type_id%TYPE;
  v_fast_id zombie_types.zombie_type_id%TYPE;
  v_tank_id zombie_types.zombie_type_id%TYPE;
  v_scale NUMBER;
  v_normal_count NUMBER;
  v_fast_count NUMBER;
  v_tank_count NUMBER;
  v_effective_wave NUMBER;

  PROCEDURE add_item(
    p_wave_id IN waves.wave_id%TYPE,
    p_zombie_type_id IN zombie_types.zombie_type_id%TYPE,
    p_count IN NUMBER,
    p_interval IN NUMBER,
    p_delay IN NUMBER
  ) IS
  BEGIN
    INSERT INTO wave_composition(wave_id, zombie_type_id, zombie_count, spawn_interval, initial_delay)
    VALUES (p_wave_id, p_zombie_type_id, GREATEST(1, CEIL(p_count)), p_interval, p_delay);
  END;
BEGIN
  SELECT zombie_type_id INTO v_normal_id FROM zombie_types WHERE zombie_type_name = UNISTR('\041E\0431\044B\0447\043D\044B\0439');
  SELECT zombie_type_id INTO v_fast_id FROM zombie_types WHERE zombie_type_name = UNISTR('\0411\044B\0441\0442\0440\044B\0439');
  SELECT zombie_type_id INTO v_tank_id FROM zombie_types WHERE zombie_type_name = UNISTR('\0422\0430\043D\043A');

  FOR wave_rec IN (
    SELECT m.map_id, m.difficulty, w.wave_id, w.wave_number
    FROM waves w
    JOIN maps m ON m.map_id = w.map_id
    ORDER BY m.map_id, w.wave_number
  ) LOOP
    v_scale := CASE wave_rec.difficulty
      WHEN 'easy' THEN 0.90
      WHEN 'normal' THEN 1.10
      ELSE 1.25
    END;

    v_effective_wave := CEIL(wave_rec.wave_number * CASE wave_rec.difficulty
      WHEN 'easy' THEN 3.0
      WHEN 'normal' THEN 3.0
      ELSE 2.85
    END);

    v_normal_count := (5 + v_effective_wave * 1.35) * v_scale;
    add_item(
      wave_rec.wave_id,
      v_normal_id,
      v_normal_count,
      GREATEST(0.55, 1.55 - v_effective_wave * 0.045),
      0
    );

    IF wave_rec.wave_number >= 2 THEN
      v_fast_count := (3 + v_effective_wave * 0.95) * v_scale;
      add_item(
        wave_rec.wave_id,
        v_fast_id,
        v_fast_count,
        GREATEST(0.42, 1.10 - v_effective_wave * 0.035),
        0.55
      );
    END IF;

    IF wave_rec.wave_number >= 3 THEN
      v_tank_count := (1 + FLOOR((v_effective_wave - 3) / 2)) * v_scale;
      add_item(
        wave_rec.wave_id,
        v_tank_id,
        v_tank_count,
        GREATEST(1.00, 2.35 - v_effective_wave * 0.055),
        1.25
      );
    END IF;
  END LOOP;
END;
/

COMMIT;
