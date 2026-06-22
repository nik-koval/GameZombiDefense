BEGIN
  EXECUTE IMMEDIATE 'CREATE ROLE zombie_defense_player';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -1921 THEN RAISE; END IF;
END;
/

GRANT EXECUTE ON zombie_defense TO zombie_defense_player;

GRANT SELECT ON players TO zombie_defense_player;
GRANT SELECT ON trick_or_treat_actions TO zombie_defense_player;
GRANT SELECT ON player_effects TO zombie_defense_player;
GRANT SELECT ON maps TO zombie_defense_player;
GRANT SELECT ON map_route TO zombie_defense_player;
GRANT SELECT ON build_points TO zombie_defense_player;
GRANT SELECT ON zombie_types TO zombie_defense_player;
GRANT SELECT ON tower_types TO zombie_defense_player;
GRANT SELECT ON waves TO zombie_defense_player;
GRANT SELECT ON wave_composition TO zombie_defense_player;
GRANT SELECT ON game_sessions TO zombie_defense_player;
GRANT SELECT ON wave_progress TO zombie_defense_player;
GRANT SELECT ON placed_towers TO zombie_defense_player;
GRANT SELECT ON active_zombies TO zombie_defense_player;
GRANT SELECT ON game_results TO zombie_defense_player;
GRANT SELECT ON v_leaderboard TO zombie_defense_player;
