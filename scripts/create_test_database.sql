-- Only run inside the dedicated shopresolve Compose MySQL service.
-- Creates an isolated, disposable test schema; does not modify the demo data.
CREATE DATABASE IF NOT EXISTS shopresolve_test CHARACTER SET utf8mb4;
GRANT ALL PRIVILEGES ON shopresolve_test.* TO 'shopresolve'@'%';
