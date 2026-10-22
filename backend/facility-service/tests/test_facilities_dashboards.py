"""Reference-data guards and persona-specific aggregate correctness."""

from uuid import uuid4

from facility.models import Building, Floor, Seat


async def test_inactive_parents_prevent_new_children_and_incidents(
    client,
    db,
    admin,
    employee,
    auth_headers,
    reference_data,
):
    """A disabled ancestor makes the location unavailable even if children remain active."""
    headers = await auth_headers(admin)
    building = reference_data["building"]
    result = await client.patch(
        f"/api/v1/buildings/{building.id}", headers=headers, json={"is_active": False}
    )
    assert result.status_code == 200, result.text
    child = await client.post(
        f"/api/v1/buildings/{building.id}/floors",
        headers=headers,
        json={
            "name": "Cannot be created",
            "level_number": 2,
        },
    )
    assert child.status_code in {409, 422}
    tree = await client.get("/api/v1/locations/tree?active_only=true", headers=headers)
    assert tree.status_code == 200
    assert str(building.id) not in tree.text
    incident = await client.post(
        "/api/v1/incidents",
        headers=await auth_headers(employee),
        json={
            "title": "Inactive building incident",
            "description": "The location is not available.",
            "building_id": str(building.id),
            "floor_id": str(reference_data["floor"].id),
            "seat_id": str(reference_data["seat"].id),
            "category_id": str(reference_data["category"].id),
        },
    )
    assert incident.status_code in {409, 422}


async def test_deactivation_keeps_history_and_refuses_nonfinal_incidents(
    client,
    db,
    admin,
    auth_headers,
    make_incident,
    reference_data,
):
    """Reference records cannot disappear from an active incident's hierarchy."""
    item = await make_incident()
    headers = await auth_headers(admin)
    for kind, key in [
        ("buildings", "building"),
        ("floors", "floor"),
        ("seats", "seat"),
    ]:
        result = await client.patch(
            f"/api/v1/{kind}/{reference_data[key].id}",
            headers=headers,
            json={"is_active": False},
        )
        assert result.status_code == 409, result.text
    item.status = "Closed"
    await db.commit()
    result = await client.patch(
        f"/api/v1/buildings/{reference_data['building'].id}",
        headers=headers,
        json={"is_active": False},
    )
    assert result.status_code == 200
    assert await db.get(Building, reference_data["building"].id) is not None
    assert await db.get(Floor, reference_data["floor"].id) is not None
    assert await db.get(Seat, reference_data["seat"].id) is not None


async def test_category_maximum_two_levels_and_duplicate_constraints(
    client,
    admin,
    auth_headers,
    reference_data,
):
    """Enforce two category levels and meaningful duplicate reference-data failures."""
    headers = await auth_headers(admin)
    child = await client.post(
        "/api/v1/categories",
        headers=headers,
        json={
            "name": "Projector",
            "parent_id": str(reference_data["category"].id),
        },
    )
    assert child.status_code == 201, child.text
    grandchild = await client.post(
        "/api/v1/categories",
        headers=headers,
        json={
            "name": "HDMI",
            "parent_id": child.json()["id"],
        },
    )
    assert grandchild.status_code in {409, 422}
    duplicate = await client.post(
        "/api/v1/buildings",
        headers=headers,
        json={
            "name": "New name",
            "code": "NORTH",
        },
    )
    assert duplicate.status_code == 409
    missing = await client.patch(
        f"/api/v1/categories/{uuid4()}", headers=headers, json={"name": "Missing"}
    )
    assert missing.status_code == 404


async def test_dashboards_count_only_their_persona_scope(
    client,
    employee,
    other_employee,
    admin,
    engineer,
    auth_headers,
    make_incident,
):
    """Count seeded facts and preserve creator/assignment boundaries in dashboards."""
    await make_incident(status="Open", priority="High")
    # The fixed reason code is separate from a vendor's free-text explanation in notes.
    await make_incident(status="Blocked", blocked_reason="Vendor needed", priority="High")
    await make_incident(status="Resolved", assigned_to=None)
    await make_incident(created_by=other_employee.id, assigned_to=None)
    creator = await client.get("/api/v1/dashboard/employee", headers=await auth_headers(employee))
    assert creator.status_code == 200, creator.text
    counts = {item["value"]: item["count"] for item in creator.json()["status_counts"]}
    assert counts == {"Open": 1, "Blocked": 1, "Resolved": 1}
    assert len(creator.json()["awaiting_confirmation"]) == 1
    technician = await client.get(
        "/api/v1/dashboard/engineer", headers=await auth_headers(engineer)
    )
    assert technician.status_code == 200, technician.text
    assert sum(row["count"] for row in technician.json()["status_counts"]) == 2
    manager = await client.get("/api/v1/dashboard/admin", headers=await auth_headers(admin))
    assert manager.status_code == 200, manager.text
    assert sum(row["count"] for row in manager.json()["status_counts"]) == 4
    assert len(manager.json()["blocked"]) == 1
    for role, target, expected in [
        (employee, "admin", 403),
        (employee, "engineer", 403),
        (engineer, "admin", 403),
        (admin, "engineer", 403),
    ]:
        response = await client.get(f"/api/v1/dashboard/{target}", headers=await auth_headers(role))
        assert response.status_code == expected


async def test_hotspots_report_each_locations_top_category(
    client, db, admin, auth_headers, make_incident, reference_data
):
    """Overview's "Top category" is the most-reported category, even below the recurring bar."""
    from facility.models import Category

    other = Category(name="Plumbing", is_active=True, sort_order=1)
    db.add(other)
    await db.commit()
    for category in [other, other, reference_data["category"]]:
        # Seat-level tickets still count towards their floor.
        await make_incident(category_id=category.id)
    result = await client.get("/api/v1/dashboard/admin", headers=await auth_headers(admin))
    assert result.status_code == 200, result.text
    floor = next(
        f for f in result.json()["hotspots"]["floors"] if f["id"] == str(reference_data["floor"].id)
    )
    assert floor["top_category_id"] == str(other.id)
    assert floor["top_category_count"] == 2
    building = result.json()["hotspots"]["buildings"][0]
    assert building["top_category_id"] == str(other.id)
