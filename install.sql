SET SERVEROUTPUT ON
ALTER SESSION SET NLS_LENGTH_SEMANTICS = CHAR;

PROMPT Creating Zombie Defense schema objects...
@@sql/01_schema.sql

PROMPT Loading demo maps, towers, zombies and waves...
@@sql/02_seed_data.sql

PROMPT Compiling PL/SQL package...
@@sql/03_package_zombie_defense.sql

PROMPT Done. You can now run the Python client.
