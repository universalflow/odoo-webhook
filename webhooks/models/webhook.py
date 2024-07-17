# -*- coding: utf-8 -*-
# Part of Odoo. See COPYRIGHT & LICENSE files for full copyright and licensing details.

import io
import json
import logging
import requests
import traceback
import datetime as dt

from odoo import models, api, fields, _, SUPERUSER_ID
from odoo.exceptions import UserError
from markupsafe import Markup
from ..utils import dicttoxml
from odoo.osv import expression

_logger = logging.getLogger(__name__)


class IrActionsServer(models.Model):
    _inherit = 'ir.actions.server'

    state = fields.Selection(selection_add = [('webhook', 'Add HTTP Callback(Webhook)')], ondelete={'webhook': 'cascade'})
    lang_id = fields.Many2one('res.lang', string='Language')
    format = fields.Selection([('json', 'JSON'), ('xml', 'XML')],
        string='Format', help='Format of the webhook payload.')
    address = fields.Char(string='Delivery URL',
        help='Webhook URL that will call when the event is triggered.')
    max_retries = fields.Integer(string='Maximum Retries',
        help='No. of retries If rendering data to given url is failed',
        default=5)
    field_ids = fields.Many2many('ir.model.fields', string='Fields')
    webhook_request_header_ids = fields.One2many('webhook.request.header', 'action_server_id', string='Headers')
    auth_id = fields.Many2one('auth.auth', 'Authentication',
        help="Key & Secret of this user is used for webhook.")

    def job_execution(self, job=None, model=None, action=None, records=None):
        job.set_started()
        values = self.env['res.config.settings'].sudo().get_values()
        timeout = values.get('timeout')
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        if job.name.startswith('Deletion of '):
            data = {model[0].get('model', False): {'id': job.res_id}}
        else:
            data = {model[0].get('model', False): records}
        if action.format == 'xml':
            formated_data = dicttoxml.dicttoxml(data, root=False, attr_type=False)
        else:
            formated_data = json.dumps(data)
        try:
            headers = {}
            for request_header in action.webhook_request_header_ids:
                headers.update({request_header.name: request_header.value})
            response = requests.post(action.address, data=formated_data, timeout=timeout, headers=headers)
        except requests.exceptions.ReadTimeout:
            raise Exception("Response is not retrieved properly.")
        except requests.exceptions.RequestException:
            raise Exception("Connection Error")
        except Exception as e:
            raise Exception("Generalized Exception")
        else:
            job.set_done(result=formated_data)
        return False

    def perform(self, model, res_id, job=None):
        """
        It sends data with authentication on deletion, updation and Creation as per given on webhook.
        on given address and format(Json/XML)
        """
        context = self._context or {}
        name = job and job.name or ''
        for rec in self:
            fields = [field.name for field in rec.field_ids]
            model = self.env['ir.model'].sudo().search_read([('model', '=', model)])
            record = {}
            if model and res_id:
                if not context.get('enqueue_jobs'):
                    records = self.env[model[0]['model']].with_context(lang=rec.lang_id.code, active_test=False).sudo().search_read([('id', '=', res_id)])
                    if fields:
                        record = {key: value for key, value in list(records[0].items()) if key in fields}
                    else:
                        record = records[0]
                for key,value in list(record.items()):
                    if isinstance(value, dt.datetime):
                        record[key] = str(value)
                    if isinstance(value, dt.date):
                        record[key] = str(value)
                    if isinstance(value, bytes):
                        record[key] = value.decode('utf-8')

                if not job:
                    name = 'old_values' not in list(context) and 'Deletion of '\
                        or context.get('old_values', False) and 'Updation of '\
                        or 'Creation of '
                    display_name = self.env[model[0]['model']].browse([res_id]).name_get()[0][1]
                    job = self.env['webhook.job'].create({
                            'name' : ' '.join([name, display_name]),
                            'date_created' : dt.datetime.now(),
                            'model_name' : model[0]['model'],
                            'server_action' : rec.id,
                            'res_id' : res_id,
                            'max_retries' : rec.max_retries,
                            'job_response': record
                        })
                    job.set_pending()
                try:
                    self.job_execution(job=job, model=model, action=rec, records=eval(job.job_response))
                except Exception:
                    buff = io.StringIO()
                    traceback.print_exc(file=buff)
                    _logger.error(buff.getvalue())
                    job.set_failed(exc_info=buff.getvalue())
        return False

    def _run_action_webhook(self, eval_context=None):
        """It is called when Given Action Triggers"""
        self.perform(self._context.get('active_model', False), self._context.get('active_id', False))
        return False


class BaseAutomation(models.Model):
    _inherit = 'base.automation'

    is_automated_action = fields.Boolean('Is Automated Action', default=True)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    max_retries = fields.Integer('Maximum Retires', default=5)
    timeout = fields.Float('Timeout', default=5)

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        IrDefault = self.env['ir.default'].sudo()
        IrDefault.set('res.config.settings', 'max_retries', self.max_retries)
        IrDefault.set('res.config.settings', 'timeout', self.timeout)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        IrDefault = self.env['ir.default'].sudo()
        if IrDefault.get('res.config.settings','max_retries') == None and IrDefault.get('res.config.settings','timeout') == None:
            res.update(max_retries=5, timeout=5)
        else:
            res.update(max_retries=IrDefault.get('res.config.settings', 'max_retries'),
                timeout= IrDefault.get('res.config.settings', 'timeout'))
        return res


class IrModel(models.Model):
    _inherit = 'ir.model'

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []
        domain = ['|',('name', operator, name), ('model', operator, name)]
        context = dict(self._context)
        if context.get('auth_id'):
            auth_id = self.env['auth.auth'].browse(context['auth_id'])
            if auth_id.user_id.id == SUPERUSER_ID:
                domain.append(('id', 'not in', []))
            else:
                group_ids = [grp.id for grp in auth_id.user_id.groups_id]
                permissions = self.env['ir.model.access'].sudo().search([('group_id', 'in', group_ids), \
                        '|', '|', \
                        ('perm_write', '=', True), \
                        ('perm_create', '=', True), \
                        ('perm_unlink', '=', True)])
                model_ids = list(set([perm.model_id.id for perm in permissions if perm.model_id]))
                domain.append(('id', 'in', model_ids))
        return self._search(expression.AND([domain, args]), limit=limit, access_rights_uid=name_get_uid)


class WebhookAction(models.Model):
    _name = 'webhook.webhook'
    _inherits = {'base.automation': 'action_rule_id'}
    _description = 'Webhook'

    _VALIDATE_FIELDS = [ 'language', 'format', 'address', 'model', 'trigger', 'name', ]
    _RETURN_FIELDS = [ 'language', 'format', 'address', 'model', 'trigger', 'name', 'create_date', 'write_date', 'active', # 'create_uid', # 'write_uid',
    ]

    action_rule_id = fields.Many2one('base.automation',
        string='Action', required=True, ondelete='cascade')
    lang_id = fields.Many2one('res.lang', string='Language',  required=True)
    language = fields.Char(related='lang_id.code', string='Language Code')
    format = fields.Selection([('json', 'JSON'), ('xml', 'XML')],
        string='Format', help='Format of the webhook payload.', required=True)
    address = fields.Char(string='Delivery URL',
        help='Payload will be delivered to this URL when event triggers.', required=True)
    field_ids = fields.Many2many('ir.model.fields', string='Fields')
    webhook_trigger = fields.Selection([('on_create', 'On Creation'),
                             ('on_write', 'On Update'),
                             ('on_unlink', 'On Deletion')], string='When to Run', required=True)
    model = fields.Char('Model', compute="_get_model", store=True)
    webhook_request_header_ids = fields.One2many('webhook.request.header', 'webhook_id', string='Headers')

    @api.constrains('webhook_trigger')
    def check_webhook_trigger(self):
        if self.webhook_trigger:
            operation = self.webhook_trigger.strip('on_')
            self.env[self.model_id.model].check_access_rights(operation, raise_exception=True)

    @api.model
    def create(self, vals):
        if vals.get('address'):
            try:
                headers = {}
                for request_header in vals.get('webhook_request_header_ids', []):
                    headers.update({request_header[2].get('name'): request_header[2].get('value')})
                request = requests.post(vals.get('address'), headers=headers)
                if request.status_code != 200:
                    raise Exception
            except Exception:
                raise UserError(_("Invalid Delivery Url or Poor Connection"))
            else:
                values = self.env['res.config.settings'].sudo().get_values()
                max_retries = values.get('max_retries')
                if self.env.context.get('from_webhook'):
                    if vals.get('trigger', False):
                        vals['webhook_trigger'] = vals.get('trigger', False)
                    if self.env.context.get('auth_id'):
                        vals['auth_id'] = self.env.context.get('auth_id')
                    if vals.get('condition', False) or vals.get('context', False) or vals.get('sort', False):
                        model_id = vals.get('model_id')
                        kind = vals.get('trigger')
                        condition = vals.get('condition', False)
                        context = vals.get('context', False)
                        sort = vals.get('sort', False)
                        vals['filter_id'],vals['filter_domain'] = self.create_filter(model_id, kind, condition, context, sort)
                        vals.pop('condition', False)
                        vals.pop('context', False)
                        vals.pop('sort', False)
                    if vals.get('fields'):
                        field_ids = self.get_field_ids(vals['fields'], vals['model_id'])
                        vals.update({'field_ids': field_ids})
                        vals.pop('fields', False)

                vals.update({'state': 'webhook', 'usage': 'ir_actions_server', 'is_automated_action': False})
                res = super(WebhookAction, self).create(vals)
                res.action_server_id.write({'lang_id': res.lang_id.id,
                     'address': res.address,
                     'format': res.format,
                     'max_retries': max_retries,
                     'field_ids': [(6, 0, res.field_ids.ids)],
                     'usage': 'ir_actions_server',
                     'auth_id': vals.get('auth_id')})
                res.webhook_request_header_ids.write({'action_server_id': res.action_server_id.id})
                return res

    def write(self, vals):
        if vals.get('model_id') or vals.get('model') or vals.get('trigger'):
            raise UserError(_('You cannot update model and event'))

        if self.env.context.get('from_webhook'):
            if 'fields' in vals.keys():
                for rec in self:
                    field_ids = rec.get_field_ids(vals['fields'])
                vals.update({'field_ids': field_ids})
                vals.pop('fields', False)
            if 'condition' in vals.keys():
                for rec in self:
                    rec.filter_domain = vals.get('condition', False)
                vals.pop('condition', False)
        for key in list(vals):
            if key not in list(self._fields):
                raise UserError(_('Invalid field name.'))
        res = super(WebhookAction, self).write(vals)
        fields = self._VALIDATE_FIELDS + ['lang_id', 'auth_id', 'max_retries', 'field_ids']
        keys = set(list(vals)).intersection(set(fields))
        action_vals = {}
        if not vals.get('action_server_id'):
            for field in keys:
                if vals.get(field):
                    action_vals[field] = vals[field]
            for rec in self:
                if rec.action_server_id:
                    rec.action_server_id.write(action_vals)
                    rec.webhook_request_header_ids.write({'action_server_id': rec.action_server_id.id})
        if vals.get('address'):
            for rec in self:
                try:
                    headers = {}
                    for request_header in rec.webhook_request_header_ids:
                        headers.update({request_header.name: request_header.value})
                    request = requests.post(vals.get('address'), headers=headers)
                    if request.status_code != 200:
                        raise Exception()
                except Exception as e:
                    raise UserError(_('Invalid Delivery Url or Poor Connection'))
                    return False
        return res

    def unlink(self):
        for rec in self:
            rec.action_server_id.unlink()
        return super(WebhookAction, self).unlink()

    @api.model
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None):
        if self._context.get('from_webhook') and not self._context.get('fields'):
            field_list = ['name', 'create_date', 'write_date',
                 'model', 'trigger', 'language', 'address', 'format',
                 'field_ids', 'filter_domain']
            records = super(WebhookAction, self).search_read(domain, field_list,offset,limit,order)
            for rec in records:
                fields = self.env['ir.model.fields'].search([('id', 'in', rec.get('field_ids'))])
                fields = [field.name for field in fields]
                rec.pop('field_ids')
                rec.update({'fields': fields})
                condition = rec.get('filter_domain') or []
                rec.pop('filter_domain')
                rec.update({'condition': condition})
            return records
        elif self._context.get('from_webhook') and self._context.get('fields'):
            if 'fields' in fields:
                fields.remove('fields')
                fields.append('field_ids')
            if 'condition' in fields:
                fields.remove('condition')
                fields.append('filter_domain')
            if 'language' in fields:
                fields.remove('language')
                fields.append('lang_id')
            records = super(WebhookAction, self).search_read(domain, fields, offset, limit, order)
            for rec in records:
                if rec.get('field_ids', False):
                    field_ids = rec.pop('field_ids')
                    fields = self.env['ir.model.fields'].search([('id', 'in', field_ids)])
                    fields = [field.name for field in fields]
                    rec.update({'fields': fields})
                if rec.get('filter_domain', False):
                    condition = rec.pop('filter_domain')
                    rec.update({'condition': condition})
                if rec.get('lang_id', False):
                    language = rec.pop('lang_id')
                    rec.update({'language': language})
            return records
        else:
            return super(WebhookAction, self).search_read(domain, fields,offset,limit,order)

    @api.depends('model_name')
    def _get_model(self):
        for rec in self:
            rec.model = rec.model_name

    @api.onchange('webhook_trigger')
    def onchange_webhook_trigger(self):
        if self.webhook_trigger:
            self.trigger = self.webhook_trigger

    @api.onchange('model_id')
    def onchange_model_id(self):
        self.model_name = False
        if self.model_id:
            self.model_name = self.model_id.model

    def get_field_ids(self, fields, model_id=False):
        if not model_id:
            model_id = self.model_id.id
        else:
            model_id = self.env['ir.model'].search([('id', '=', model_id)]).id
        if fields:
            fields = isinstance(fields, list) and fields or fields.split(',')
            fields = [field.strip() for field in fields]
            field_ids = self.env['ir.model.fields'].search([('model_id', '=', model_id), ('name', 'in', fields)]).ids
            return [(6, 0, field_ids)]
        else:
            return [(6, 0, [])]

    def display_jobs(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Jobs'),
            'res_model': 'webhook.job',
            'view_mode': 'tree,form',
            'domain': [('server_action', 'in', self.action_server_id.ids)],
        }

    def test_notification(self):
        data = {'name': self.name,
                'create_date': fields.Datetime.to_string(self.create_date),
                'write_date': fields.Datetime.to_string(self.write_date),
                'model': self.model_id.name,
                'trigger': self.trigger,
                'language': self.lang_id.name,
                'address': self.address,
                'format': self.format,
                'fields': self.field_ids and [field_id.name for field_id in self.field_ids] or [],
                'condition': self.filter_domain or [],
                'trigger_field_ids': self.trigger_field_ids.ids,
                'groups_id': self.groups_id.ids,
                }
        try:
            headers = {}
            for request_header in self.webhook_request_header_ids:
                headers.update({request_header.name: request_header.value})
            requests.post(self.address, data=json.dumps(data), headers=headers)
        except Exception as e:
            raise UserError(e)


class WebhookRequestHeader(models.Model):
    _name = 'webhook.request.header'
    _description = 'Webhook Request Header'

    webhook_id = fields.Many2one('webhook.webhook', string='Webhook')
    action_server_id = fields.Many2one('ir.actions.server', string='Action Server')
    name = fields.Char(string='Key', required=True)
    value = fields.Char(string='Value', required=True)

    _sql_constraints = [
           ('header_uniq', 'unique (name, webhook_id)', "Duplicate headers are exist !"),
       ]

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
