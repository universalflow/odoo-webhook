# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

from odoo import models, fields, api, exceptions, _
from datetime import datetime, timedelta
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as df

STATES = [('pending', 'Pending'),
          ('enqueued', 'Enqueued'),
          ('started', 'Started'),
          ('done', 'Done'),
          ('failed', 'Failed')]


class QueueJob(models.Model):
    """ Job status and result """
    _name = 'webhook.job'
    _description = 'Webhook Job'
    _inherit = ['mail.thread']
    _order = 'date_created DESC, date_done DESC'

    name = fields.Char(string='Name', readonly=True)
    state = fields.Selection(STATES,
                             string='State',
                             default='pending',
                             readonly=True,
                             required=True,
                             index=True)
    priority = fields.Integer(readonly=True)
    exc_info = fields.Text(string='Exception Info', readonly=True)
    result = fields.Text(string='Result', readonly=True)
    date_created = fields.Datetime(string='Created Date', readonly=True)
    date_started = fields.Datetime(string='Start Date', readonly=True)
    date_enqueued = fields.Datetime(string='Enqueue Time', readonly=True)
    date_done = fields.Datetime(string='Date Done', readonly=True)
    model_name = fields.Char(string='Related Model', readonly=True)
    server_action = fields.Many2one(
        'ir.actions.server',
        string='Server Action',
        readonly=True)
    delivery_url = fields.Char(related='server_action.address',
        string="Delivery URL",
        readonly=True)
    res_id = fields.Integer(string='Related Resource ID', readonly=True)
    retry = fields.Integer(string='Current try')
    max_retries = fields.Integer(
        string='Maximum Retries',
        default=5,
        help="The job will fail if the number of tries reach the "
             "max. retries.\n"
             "Retries are infinite when empty or 0.",
    )
    job_response = fields.Text(string='Job Response')

    def set_pending(self):
        """Set to pending state"""
        for record in self:
            record.state = 'pending'
            record.date_enqueued = None
            record.date_started = None

    def requeue(self):
        """Requeue the jobs"""
        for job in self:
            job.retry = 0
            job.set_pending()

    def set_enqueued(self, retry=True):
        """Increment Trials as well as requeue the job"""
        for record in self:
            record.state = 'enqueued'
            record.date_enqueued = datetime.today()
            record.date_started = None
            if retry and record.max_retries:
                record.retry+=1

    def set_started(self):
        """Sets the job state to start"""
        for record in self:
            record.state = 'started'
            record.date_started = datetime.today()

    def set_done(self, result=None):
        """Job in done state"""
        for record in self:
            record.state = 'done'
            record.exc_info = None
            record.date_done = datetime.today()
            if result:
                record.result = result

    def set_failed(self, exc_info=None):
        """set to Fail State"""
        for record in self:
            record.state = 'failed'
            if exc_info is not None:
                record.exc_info = exc_info

    @api.model
    def enqueue_jobs(self):
        """Schedular for enqueue job which are started"""
        jobs = self.search([('state', 'in', ['pending', 'enqueued', 'failed'])])
        filtered_job_ids = [job.id for job in jobs if not job.max_retries or job.retry < job.max_retries]
        filtered_jobs = self.search([('id', 'in', filtered_job_ids)])
        filtered_jobs.set_enqueued()
        for job in filtered_jobs:
            job.server_action.with_context(enqueue_jobs=True).perform(\
                job.model_name, job.res_id, job=job)

    def job_cron(self):
        check_date = datetime.now()-timedelta(days=3)
        self.env['webhook.job'].search([('date_created', '<', check_date.strftime(df)), ('state', '=', 'done')]).unlink()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: