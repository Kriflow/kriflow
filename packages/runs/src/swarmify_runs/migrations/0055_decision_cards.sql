-- Multi-pick: the cards a pick gate's answer picked (in pick order), parked and rejected (with
-- reasons), and their titles, as JSON {"artifacts", "parked", "rejected", "titles"}. NULL for a
-- decision with no cards.
ALTER TABLE gate_decisions ADD COLUMN cards TEXT;
