"""Presentation policy for parser diagnostics, including historical stored messages.

Only known routine messages are hidden. Unknown warnings and real gaps stay visible;
this function never certifies completeness or changes stored parsing results.
"""

import re

DRAWING_NOTICE = '图纸已建立检索索引；尺寸和结构请结合原图查看。'
REVISION_NOTICE = '文件含修订记录，已按当前正文读取。'
STORED_NOTICE = '此格式仅保存原件，暂不提供正文检索。'

_ROUTINE = (
    re.compile(r'^文本中的不可见空字符已替换为空格；原文件保留。$'),
    re.compile(r'^PDF 表格按原页保留；跨页表头和脚注需结合相邻页读取。$'),
    re.compile(r'^OCR 识别文字未经人工核对；表格结构与图形语义可能不完整，关键数字请核对原文件。$'),
    re.compile(r'^Word (?:文本框内容已收录|内容控件内容已收录)（\d+ (?:个文本框|处)），版面位置(?:可能)?与原文件不同。$'),
    re.compile(r'^Word \d+ 张内容图已生成 AI 摘要；图中细节请核对原文件。$'),
    re.compile(r'^Excel \d+ 张内容图片已生成 AI 转述；细节请核对原文件。$'),
    re.compile(r'^工作表“.+”保留公式与已有缓存结果，未重新计算公式。$'),
    re.compile(r'^(?:PPT|PDF) \d+ 页视觉内容已生成 AI 转述；细节请核对原文件。$'),
    re.compile(r'^PDF \d+ 页扫描正文采用 OCR 提取；未调用视觉 API。$'),
    re.compile(r'^PDF 第 \d+ 页为扫描页，已 OCR 识别；关键数字请核对原文件。$'),
    re.compile(r'^PDF 已生成 \d+ 个视觉分区转写，\d+ 页全部分区完成且未发现数字线索遗漏；细节请核对原文件。$'),
    re.compile(r'^图片(?:正文已完成 OCR 提取|已完成 OCR 和 AI 转述；细节请核对原文件)。$'),
    re.compile(r'^视觉分区处理已保存进度，排队继续处理。$'),
    re.compile(r'^执行器重载，自动重新排队。$'),
)
_DRAWING = re.compile(
    r'^PDF 第 \d+ 页按图纸建立检索索引；图名、图号、说明和明细请结合原图核对，不转述全图尺寸。$'
)
_CAD_SCOPE = {
    'CAD 索引范围仅限原生文字和块属性；图形及精确尺寸关系请以原图为准。',
    '图纸检索线索，须结合原图读取；不用于确定尺寸、几何关系或工程结论。',
}


def public_warnings(messages):
    """Deduplicate and hide routine diagnostics without hiding specific problems."""
    result = []
    for message in messages or []:
        if any(pattern.fullmatch(message) for pattern in _ROUTINE):
            continue
        if _DRAWING.fullmatch(message) or message in _CAD_SCOPE:
            message = DRAWING_NOTICE
        elif message == 'Word 含修订记录，已按当前正文收录插入内容并排除删除内容；历史修订请查看原文件。':
            message = REVISION_NOTICE
        elif message == '此格式仅保存原文件，暂无正文索引；旧 Office 请另提交 .docx/.xlsx/.pptx，CAD／模型包请关联说明或参数文件。':
            message = STORED_NOTICE
        if message not in result:
            result.append(message)
    return result
