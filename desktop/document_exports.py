"""Bounded, in-memory document exports for an authenticated browser session."""
import asyncio
from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape

from aiohttp import web
from desktop.workspace_connections import safe_cell

MIME={'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'pdf':'application/pdf'}


def tables(content):
    found=[];current=[]
    for line in content.splitlines()+['']:
        if line.strip().startswith('|') and line.strip().endswith('|'):
            cells=[cell.strip() for cell in line.strip().strip('|').split('|')]
            if not all(re.fullmatch(r':?-{2,}:?',cell.replace(' ','')) for cell in cells):current.append(cells)
        elif current:
            found.append(current);current=[]
    if len(found)>12 or any(len(table)>2000 or any(len(row)>30 for row in table) for table in found):raise ValueError('document_too_large')
    return found


def markdown_blocks(content):
    blocks=[];lines=content.splitlines();index=0
    while index<len(lines):
        line=lines[index]
        if line.strip().startswith('|') and line.strip().endswith('|'):
            group=[]
            while index<len(lines) and lines[index].strip().startswith('|') and lines[index].strip().endswith('|'):
                group.append(lines[index]);index+=1
            for table in tables('\n'.join(group)):blocks.append(('table',table))
            continue
        if line.startswith('#'):blocks.append(('heading',line.lstrip('# ').strip()))
        elif line.lstrip().startswith(('- ','* ')):blocks.append(('bullet',line.lstrip()[2:]))
        else:blocks.append(('paragraph',line))
        index+=1
    return blocks


def export_document(kind, content, title='Ответ Велии'):
    output=BytesIO();content=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',content)
    if kind=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        workbook=Workbook();workbook.remove(workbook.active)
        data=tables(content) or [[['Строка','Текст']]+[[str(index+1),line] for index,line in enumerate(content.splitlines())]]
        for index,rows in enumerate(data):
            sheet=workbook.create_sheet('Лист '+str(index+1));sheet.freeze_panes='A2'
            for row in rows:
                values=[]
                for value in row:
                    if isinstance(value,str) and len(value)<16 and re.fullmatch(r'-?\d+(?:\.\d+)?',value):value=float(value) if '.' in value else int(value)
                    values.append(safe_cell(value))
                sheet.append(values)
            for cell in sheet[1]:cell.font=Font(bold=True);cell.fill=PatternFill('solid',fgColor='EEEEEE')
            for column in sheet.columns:sheet.column_dimensions[column[0].column_letter].width=min(60,max(14,max(len(str(cell.value or '')) for cell in column)+2))
        workbook.save(output)
    elif kind=='docx':
        from docx import Document
        from docx.shared import Pt
        document=Document();document.styles['Normal'].font.size=Pt(11)
        sections=markdown_blocks(content)
        heading=sections.pop(0)[1] if sections and sections[0][0]=='heading' else title
        document.add_heading(heading,0)
        for kind_,value in sections:
            if kind_=='table':
                width=max(len(row) for row in value);table=document.add_table(rows=0,cols=width);table.style='Light Shading Accent 1'
                for row in value:
                    cells=table.add_row().cells
                    for index,cell in enumerate(row):cells[index].text=cell
            elif kind_=='heading':document.add_heading(value,2)
            elif kind_=='bullet':document.add_paragraph(value,style='List Bullet')
            else:document.add_paragraph(value)
        document.save(output)
    elif kind=='pptx':
        from pptx import Presentation
        from pptx.util import Pt
        presentation=Presentation();chunks=[];heading=title;body=[]
        for line in content.splitlines():
            if line.startswith('#'):
                if body:chunks.append((heading,body));body=[]
                heading=line.lstrip('# ').strip()[:100];continue
            for start in range(0,len(line),300):
                piece=line[start:start+300]
                if not piece.strip():continue
                if len(body)>=6 or sum(len(value) for value in body)+len(piece)>800:
                    chunks.append((heading,body));body=[]
                body.append(piece)
        if body or not chunks:chunks.append((heading,body))
        if len(chunks)>60:raise ValueError('document_too_large')
        for heading,body in chunks:
            slide=presentation.slides.add_slide(presentation.slide_layouts[1]);slide.shapes.title.text=heading
            for paragraph in slide.shapes.title.text_frame.paragraphs:paragraph.font.size=Pt(28)
            frame=slide.placeholders[1].text_frame;frame.word_wrap=True
            for index,line in enumerate(body):
                paragraph=frame.paragraphs[0] if not index else frame.add_paragraph();paragraph.text=line;paragraph.font.size=Pt(18)
        presentation.save(output)
    elif kind=='pdf':
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4,landscape
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        folder=Path('/usr/share/fonts/truetype/dejavu')
        if not (folder/'DejaVuSans.ttf').is_file():raise ValueError('document_font_unavailable')
        for name,file in [('VeliaUnicode','DejaVuSans.ttf'),('VeliaUnicodeBold','DejaVuSans-Bold.ttf')]:
            if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(folder/file)))
        styles=getSampleStyleSheet()
        for name in ['Normal','Title','Heading2']:styles[name].fontName='VeliaUnicodeBold' if name!='Normal' else 'VeliaUnicode'
        styles['Normal'].fontSize=10;styles['Normal'].leading=15
        sections=markdown_blocks(content)
        heading=sections.pop(0)[1] if sections and sections[0][0]=='heading' else title
        columns=max([len(row) for kind_,value in sections if kind_=='table' for row in value] or [1])
        if columns>12:raise ValueError('document_table_too_wide')
        page=landscape(A4) if columns>6 else A4
        flow=[Paragraph(escape(heading),styles['Title']),Spacer(1,16)]
        for kind_,value in sections:
            if kind_=='table':
                width=max(len(row) for row in value)
                cells=[[Paragraph(escape(cell),styles['Normal']) for cell in row]+['']*(width-len(row)) for row in value]
                table=Table(cells,colWidths=[(page[0]-88)/width]*width,repeatRows=1,hAlign='LEFT')
                table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eeeeee')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#cccccc')),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
                flow.extend([table,Spacer(1,12)]);continue
            style=styles['Heading2'] if kind_=='heading' else styles['Normal']
            flow.append(Paragraph(escape(('• ' if kind_=='bullet' else '')+value) or ' ',style));flow.append(Spacer(1,7))
        SimpleDocTemplate(output,pagesize=page,leftMargin=44,rightMargin=44,title=heading,author='VELIA').build(flow)
    else:raise ValueError('invalid_document_format')
    return output.getvalue()


def setup_document_exports(app, *, session_for, same_origin, json_response):
    slots=asyncio.Semaphore(2)
    async def download(request):
        if not same_origin(request):return json_response({'ok':False,'error':'invalid_origin'},403)
        session=await session_for(request)
        if not session:return json_response({'ok':False,'error':'unauthorized'},401)
        if slots.locked():return json_response({'ok':False,'error':'document_busy'},429)
        try:
            if request.content_length and request.content_length>350000:raise ValueError()
            data=await request.json();content=data.get('content');kind=data.get('format')
            if kind not in MIME or not isinstance(content,str) or not 1<=len(content)<=50000:raise ValueError()
            async with slots:body=await asyncio.to_thread(export_document,kind,content)
            return web.Response(body=body,content_type=MIME[kind],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'attachment; filename="VELIA-answer.'+kind+'"'})
        except (ValueError,TypeError,AttributeError):return json_response({'ok':False,'error':'invalid_document'},400)
    app.router.add_post('/web-api/v1/documents/export',download)
