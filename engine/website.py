from datetime import datetime, timedelta

import socket

from twisted.web import resource, static
from twisted.application.service import IServiceCollection

from scrapy.utils.misc import load_object

from .interfaces import IPoller, IEggStorage, ISpiderScheduler

from six.moves.urllib.parse import urlparse


class Root(resource.Resource):
    """
    提供简易ui功能
    """
    def __init__(self, config, app):
        resource.Resource.__init__(self)
        self.debug = config.getboolean('debug', False)
        self.runner = config.get('runner')
        logsdir = config.get('logs_dir')
        itemsdir = config.get('items_dir')
        local_items = itemsdir and (urlparse(itemsdir).scheme.lower() in ['', 'file'])
        self.app = app
        self.nodename = config.get('node_name', socket.gethostname())
        self.putChild(b'', Home(self, local_items))
        if logsdir:
            self.putChild(b'logs', static.File(logsdir.encode('ascii', 'ignore'), 'text/plain'))
        if local_items:
            self.putChild(b'items', static.File(itemsdir, 'text/plain'))
        self.putChild(b'jobs', Jobs(self, local_items))
        services = config.items('services', ())
        for servName, servClsName in services:
          servCls = load_object(servClsName)
          self.putChild(servName.encode('utf-8'), servCls(self))
        self.update_projects()

    def update_projects(self):
        self.poller.update_projects()
        self.scheduler.update_projects()

    @property
    def launcher(self):
        app = IServiceCollection(self.app, self.app)
        return app.getServiceNamed('launcher')

    @property
    def scheduler(self):
        return self.app.getComponent(ISpiderScheduler)

    @property
    def eggstorage(self):
        return self.app.getComponent(IEggStorage)

    @property
    def poller(self):
        return self.app.getComponent(IPoller)


class Home(resource.Resource):

    def __init__(self, root, local_items):
        resource.Resource.__init__(self)
        self.root = root
        self.local_items = local_items

    def render_GET(self, txrequest):
        vars = {
            'projects': ', '.join(self.root.scheduler.list_projects())
        }
        s = """
<html>
<head>
<title>DtCrawlEngine</title>
<meta http-equiv="Content-type" content="text/html;charset=UTF-8">
<meta charset="UTF-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="">
<meta name="author" content="dt-stack">
</head>
<body>
<h1>DtCrawlEngine</h1>
<p>有效工程: <b>%(projects)s</b></p>
<ul>
<li><a href="/jobs">任务列表</a></li>
""" % vars
        if self.local_items:
            s += '<li><a href="/items/">Items</a></li>'
        s += """
<li><a href="/logs/">日志列表</a></li>
</ul>

<h2>如何调度爬虫?</h2>

<p>通过api方式来调度</p>

<p>使用案例 <a href="http://curl.haxx.se/">curl</a>:</p>
<p><code>curl http://localhost:6800/schedule.json -d project=default -d spider=爬虫脚本名称</code></p>
</body>
</html>
""" % vars
        return s.encode('utf-8')


def microsec_trunc(timelike):
    if hasattr(timelike, 'microsecond'):
        ms = timelike.microsecond
    else:
        ms = timelike.microseconds
    return timelike - timedelta(microseconds=ms)


class Jobs(resource.Resource):

    def __init__(self, root, local_items):
        resource.Resource.__init__(self)
        self.root = root
        self.local_items = local_items

    cancel_button = """
    <form method="post" action="/cancel.json">
    <input type="hidden" name="project" value="{project}"/>
    <input type="hidden" name="job" value="{jobid}"/>
    <input type="submit" style="float: left;" value="Cancel"/>
    </form>
    """.format

    header_cols = [
        'Project', 'Spider',
        'Job', 'PID',
        'Start', 'Runtime', 'Finish',
        'Log', 'Items',
        'Cancel',
    ]

    def gen_css(self):
        css = [
            '#jobs>thead td {text-align: center; font-weight: bold}',
            '#jobs>tbody>tr:first-child {background-color: #eee}',
        ]
        if not self.local_items:
            col_idx = self.header_cols.index('Items') + 1
            css.append('#jobs>*>tr>*:nth-child(%d) {display: none}' % col_idx)
        if 'cancel.json' not in self.root.children:
            col_idx = self.header_cols.index('Cancel') + 1
            css.append('#jobs>*>tr>*:nth-child(%d) {display: none}' % col_idx)
        return '\n'.join(css)

    def prep_row(self, cells):
        if not isinstance(cells, dict):
            assert len(cells) == len(self.header_cols)
        else:
            cells = [cells.get(k) for k in self.header_cols]
        cells = ['<td>%s</td>' % ('' if c is None else c) for c in cells]
        return '<tr>%s</tr>' % ''.join(cells)

    def prep_doc(self):
        return (
            '<html>'
            '<head>'
            '<meta charset="UTF-8">'
            '<title>DtCrawlEngine</title>'
            '<style type="text/css">' + self.gen_css() + '</style>'
            '</head>'
            '<body><h1>任务列表</h1>'
            '<p><a href="..">返回</a></p>'
            + self.prep_table() +
            '</body>'
            '</html>'
        )

    def prep_table(self):
        return (
            '<table id="jobs" style="width:100%;empty-cells: show;border: 1px solid #cbcbcb">'
            '<thead>' + self.prep_row(self.header_cols) + '</thead>'
            '<tbody>'
            + '<tr><th colspan="%d">待运行</th></tr>' % len(self.header_cols)
            + self.prep_tab_pending() +
            '</tbody>'
            '<tbody>'
            + '<tr><th colspan="%d">运行中</th></tr>' % len(self.header_cols)
            + self.prep_tab_running() +
            '</tbody>'
            '<tbody>'
            + '<tr><th colspan="%d">已完成</th></tr>' % len(self.header_cols)
            + self.prep_tab_finished() +
            '</tbody>'
            '</table>'
        )

    def prep_tab_pending(self):
        return '\n'.join(
            self.prep_row(dict(
                Project=project, Spider=m['name'], Job=m['_job'],
                Cancel=self.cancel_button(project=project, jobid=m['_job'])
            ))
            for project, queue in self.root.poller.queues.items()
            for m in queue.list()
        )

    def prep_tab_running(self):
        return '\n'.join(
            self.prep_row(dict(
                Project=p.project, Spider=p.spider,
                Job=p.job, PID=p.pid,
                Start=microsec_trunc(p.start_time),
                Runtime=microsec_trunc(datetime.now() - p.start_time),
                Log='<a href="/logs/%s/%s/%s.log">Log</a>' % (p.project, p.spider, p.job),
                Items='<a href="/items/%s/%s/%s.jl">Items</a>' % (p.project, p.spider, p.job),
                Cancel=self.cancel_button(project=p.project, jobid=p.job)
            ))
            for p in self.root.launcher.processes.values()
        )

    def prep_tab_finished(self):
        return '\n'.join(
            self.prep_row(dict(
                Project=p.project, Spider=p.spider,
                Job=p.job,
                Start=microsec_trunc(p.start_time),
                Runtime=microsec_trunc(p.end_time - p.start_time),
                Finish=microsec_trunc(p.end_time),
                Log='<a href="/logs/%s/%s/%s.log">Log</a>' % (p.project, p.spider, p.job),
                Items='<a href="/items/%s/%s/%s.jl">Items</a>' % (p.project, p.spider, p.job),
            ))
            for p in self.root.launcher.finished
        )

    def render(self, txrequest):
        doc = self.prep_doc()
        txrequest.setHeader('Content-Type', 'text/html; charset=utf-8')
        txrequest.setHeader('Content-Length', len(doc))
        return doc.encode('utf-8')
