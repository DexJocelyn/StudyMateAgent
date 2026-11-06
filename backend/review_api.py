import asyncio
from fastapi import APIRouter, Request
from pydantic import Field
from backend.services.models import StrictModel
from backend.services import review
from backend.services.tools import BusinessError
from backend.mcp_client import open_tools

router=APIRouter(prefix='/api/review')

class Instructions(StrictModel):
    text: str = Field(min_length=4,max_length=12000)
class Material(StrictModel):
    name: str = Field(min_length=1,max_length=120)
    content: str = Field(min_length=4,max_length=18000)
class Upload(StrictModel):
    name: str = Field(min_length=1,max_length=120)
    content_base64: str = Field(max_length=710000)
class Analyze(StrictModel):
    request_id: str = Field(min_length=1,max_length=100)
class Human(StrictModel):
    run_id: str
    index: int = Field(ge=0,le=39)
    status: str
    note: str = Field(default='',max_length=1000)
class Export(StrictModel):
    run_id: str
class Followup(StrictModel):
    run_id: str
    index: int = Field(ge=0,le=39)
    assignee_id: str
    deadline: str
    estimated_minutes: int = Field(strict=True,gt=0,le=10080)

@router.get('/{project_id}')
def workspace(project_id:str):
    return review.get_workspace(project_id)

@router.get('/{project_id}/summary')
def summary(project_id:str):
    return review.project_summary(project_id)

@router.put('/{project_id}/instructions')
def instructions(project_id:str,body:Instructions):
    return review.set_instructions(project_id,body.text)

@router.post('/{project_id}/materials')
def material(project_id:str,body:Material):
    return review.add_material(project_id,body.name,body.content)

@router.post('/{project_id}/instructions/upload')
def upload_instructions(project_id:str,body:Upload):
    return review.set_instructions(project_id,review.read_file(body.name,body.content_base64))

@router.post('/{project_id}/upload')
def upload(project_id:str,body:Upload):
    return review.add_material(project_id,body.name,review.read_file(body.name,body.content_base64))

@router.delete('/{project_id}/materials/{material_id}')
def remove(project_id:str,material_id:str):
    return review.remove_material(project_id,material_id)

@router.post('/{project_id}/analyze')
async def analyze(project_id:str,body:Analyze,request:Request):
    lock=request.app.state.locks.setdefault('review:'+project_id,asyncio.Lock())
    if lock.locked():
        raise BusinessError('REQUEST_PENDING','这个项目正在自查，请稍候，不要重复点击。')
    async with lock:
        return await review.analyze(project_id,body.request_id)

@router.patch('/{project_id}/human')
def human(project_id:str,body:Human):
    return review.human_review(project_id,body.run_id,body.index,body.status,body.note)

@router.post('/{project_id}/export')
def export(project_id:str,body:Export):
    return review.export_review(project_id,body.run_id)

@router.post('/{project_id}/tasks')
async def followup(project_id:str,body:Followup):
    async with open_tools() as client:
        result=await client.call('create_review_task',body.model_dump(),project_id,'review-task:'+body.run_id+':'+str(body.index))
    if not result['ok']:
        raise BusinessError(result['error']['code'],result['error']['message'])
    return result
