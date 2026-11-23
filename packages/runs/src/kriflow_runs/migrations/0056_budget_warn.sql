-- The spend at which `budget.warning` fires, from the workflow or the start body. NULL warns at
-- 80 percent of the Budget, as before.
ALTER TABLE runs ADD COLUMN budget_warn_usd REAL;
