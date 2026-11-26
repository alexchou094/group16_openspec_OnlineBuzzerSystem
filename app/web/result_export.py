from typing import Annotated

from fastapi import Depends, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.repositories.memory import Session


class ConfirmExport(BaseModel):
    preview_id: str


def register_export_routes(app, templates, exporter, existing_session, authorized_mutation, writer_factory):
    Existing = Annotated[Session, Depends(existing_session)]
    Mutation = Annotated[Session, Depends(authorized_mutation)]
    base = "/activities/{activity_id}/exports"

    @app.get(base, include_in_schema=False)
    def export_page(activity_id: str, request: Request, session: Existing):
        preview = exporter.preview(activity_id, session.id)
        return templates.TemplateResponse(request=request, name="result_export.html", context={
            "activity_id": activity_id, "csrf": session.csrf, "preview": preview,
            "attempt": exporter.attempt_view(activity_id, session.id),
        })

    @app.post(base + "/preview")
    def preview_export(activity_id: str, session: Mutation):
        preview = exporter.preview(activity_id, session.id)
        return {
            "id": preview.id, "revision": preview.revision,
            "sheet_title": preview.source.title if preview.source else None,
            "cells": preview.cells,
        }

    @app.post(base + "/write")
    async def write_export(activity_id: str, body: ConfirmExport, session: Mutation):
        return await run_in_threadpool(
            exporter.run, activity_id, session.id, writer_factory(activity_id), preview_id=body.preview_id,
        )

    @app.get(base + "/results.csv")
    def download_csv(activity_id: str, session: Existing):
        return Response(exporter.csv(activity_id, session.id), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="buzzer-results.csv"'})

    @app.get(base + "/attempts/{attempt_id}")
    def export_status(activity_id: str, attempt_id: str, session: Existing):
        return exporter.attempt_view(activity_id, session.id, attempt_id)

    @app.post(base + "/attempts/{attempt_id}/verify")
    async def verify_export(activity_id: str, attempt_id: str, session: Mutation):
        return await run_in_threadpool(
            exporter.run, activity_id, session.id, writer_factory(activity_id), attempt_id=attempt_id, verify_only=True,
        )

    @app.post(base + "/attempts/{attempt_id}/retry")
    async def retry_export(activity_id: str, attempt_id: str, session: Mutation):
        return await run_in_threadpool(
            exporter.run, activity_id, session.id, writer_factory(activity_id), attempt_id=attempt_id,
        )
