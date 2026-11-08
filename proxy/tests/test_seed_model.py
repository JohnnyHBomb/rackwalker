"""The synthetic seed network has the documented shape."""
import seed_model as sm


def test_counts():
    m = sm.build()
    assert (len(m.sites), len(m.locations), len(m.racks), len(m.devices), len(m.cables), len(m.circuits)) == (3, 8, 12, 60, 82, 2)
    assert sum(1 for d in m.devices if d.rack is None) == 4
    assert {d.role for d in m.devices} == set(sm.ROLES)
    assert len({c.label for c in m.cables}) == len(m.cables)


def test_no_port_used_twice():
    m = sm.build()
    ends = [e for c in m.cables for e in (c.a, c.b)]
    assert len(ends) == len(set(ends))
