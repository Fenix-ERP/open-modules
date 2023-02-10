from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import formatLang

class KsGlobalDiscountInvoice(models.Model):
    # _inherit = "account.invoice"
    """ changing the model to account.move """
    _inherit = "account.move"

    ks_global_discount_type = fields.Selection(
        [("percent", "Percentage"), ("amount", "Amount")],
        string="Universal Discount Type",
        readonly=True,
        states={"draft": [("readonly", False)], "sent": [("readonly", False)]},
        default="percent",
    )
    ks_global_discount_rate = fields.Float(
        "Universal Discount Rate",
        readonly=True,
        states={"draft": [("readonly", False)], "sent": [("readonly", False)]},
    )
    ks_amount_discount = fields.Monetary(
        string="Universal Discount",
        readonly=True,
        compute="_compute_amount",
        store=True,
        tracking=True,
    )
    ks_enable_discount = fields.Boolean(compute="_compute_verify_discount")
    ks_sales_discount_account_id = fields.Integer(compute="_compute_verify_discount")
    ks_purchase_discount_account_id = fields.Integer(compute="_compute_verify_discount")
    ks_total_amount = fields.Monetary(string='Total Amount', store=True, readonly=True, tracking=True,
        compute='_compute_amount')

    @api.depends("company_id.ks_enable_discount")
    def _compute_verify_discount(self):
        for rec in self:
            rec.ks_enable_discount = rec.company_id.ks_enable_discount
            rec.ks_sales_discount_account_id = (
                rec.company_id.ks_sales_discount_account.id
            )
            rec.ks_purchase_discount_account_id = (
                rec.company_id.ks_purchase_discount_account.id
            )

    # 1. tax_line_ids is replaced with tax_line_id. 2. api.mulit is also removed.
    @api.depends(
        "line_ids.debit",
        "line_ids.credit",
        "line_ids.currency_id",
        "line_ids.amount_currency",
        "line_ids.amount_residual",
        "line_ids.amount_residual_currency",
        "line_ids.payment_id.state",
        "ks_global_discount_type",
        "ks_global_discount_rate",
    )
    def _compute_amount(self):
        super(KsGlobalDiscountInvoice, self)._compute_amount()
        for rec in self:
            if not ("ks_global_tax_rate" in rec):
                rec.ks_calculate_discount()
            sign = rec.move_type in ["in_refund", "out_refund"] and -1 or 1
            #     rec.amount_residual = rec.amount_total
            rec.amount_residual_signed = rec.amount_total * sign
            rec.amount_total_signed = rec.amount_total * sign

    def ks_calculate_discount(self):
        for rec in self:
            if rec.ks_global_discount_type == "amount":
                rec.ks_amount_discount = (
                    rec.ks_global_discount_rate if rec.amount_untaxed > 0 else 0
                )
            elif rec.ks_global_discount_type == "percent":
                if rec.ks_global_discount_rate != 0.0:
                    rec.ks_amount_discount = (
                        (rec.amount_untaxed + rec.amount_tax)
                        * rec.ks_global_discount_rate
                        / 100
                    )
                else:
                    rec.ks_amount_discount = 0
            elif not rec.ks_global_discount_type:
                rec.ks_global_discount_rate = 0
                rec.ks_amount_discount = 0
            rec.ks_total_amount= (
                rec.amount_untaxed
            )
            rec.amount_untaxed = (
                rec.amount_untaxed - rec.ks_amount_discount
            )
            rec.amount_tax = (
                rec.amount_tax - (rec.ks_amount_discount * 0.12)
            )
            rec._compute_invoice_taxes_by_group()
            new_amount_by_group = []
            for amount in rec.amount_by_group:
                new_value = []
                value = list(amount)
                dis = rec.ks_amount_discount * 0.12
                if 'IVA' in value[0]: 
                    new_value.append(value[0])
                    new_value.append(value[1] - dis)
                    new_value.append(value[2])
                    new_value.append(formatLang(self.env, value[1] - dis, currency_obj=rec.currency_id))
                    new_value.append(value[4]) 
                    new_value.append(value[5])
                    new_value.append(value[6])           
                    if isinstance(value,tuple):
                        value = tuple(new_value)
                    else:
                        value = new_value
                    new_amount_by_group.append(value)
                else:
                    new_amount_by_group.append(amount)
            rec.amount_by_group = new_amount_by_group
            rec.amount_total = (
                rec.amount_tax + rec.amount_untaxed
            )
            rec.ks_update_universal_discount()

    @api.constrains("ks_global_discount_rate")
    def ks_check_discount_value(self):
        if self.ks_global_discount_type == "percent":
            if self.ks_global_discount_rate > 100 or self.ks_global_discount_rate < 0:
                raise ValidationError(
                    _("You cannot enter percentage value greater than 100.")
                )
        else:
            if self.ks_global_discount_rate < 0 or self.amount_untaxed < 0:
                raise ValidationError(
                    _(
                        "You cannot enter discount amount greater"
                        " than actual cost or value lower than 0."
                    )
                )

    @api.model
    def _prepare_refund(
        self, invoice, date_invoice=None, date=None, description=None, journal_id=None
    ):
        ks_res = super(KsGlobalDiscountInvoice, self)._prepare_refund(
            invoice, date_invoice=None, date=None, description=None, journal_id=None
        )
        ks_res["ks_global_discount_rate"] = self.ks_global_discount_rate
        ks_res["ks_global_discount_type"] = self.ks_global_discount_type
        return ks_res

    def ks_update_universal_discount(self):
        """This Function Updates the Universal Discount through Sale Order"""
        for rec in self:
            already_exists = self.line_ids.filtered(
                lambda line: line.name and line.name.find("Universal Discount") == 0
            )
            terms_lines = self.line_ids.filtered(
                lambda line: line.account_id.user_type_id.type
                in ("receivable", "payable")
            )
            other_lines = self.line_ids.filtered(
                lambda line: line.account_id.user_type_id.type
                not in ("receivable", "payable")
            )
            account_iva = self.line_ids.filtered(
                lambda line: line.name and line.name.find("IVA") == 0
            )
            if account_iva and rec.ks_amount_discount > 0:
                amount = rec.amount_tax*-1
                if (
                    (
                        rec.move_type == "out_invoice" or rec.move_type == "out_refund"
                    )
                ):
                    if rec.move_type == "out_invoice":
                        account_iva.update(
                            {
                                "debit": amount > 0.0 and amount or 0.0,
                                "credit": amount < 0.0 and -amount or 0.0,
                            }
                        )
                    else:
                        account_iva.update(
                            {
                                "debit": amount < 0.0 and -amount or 0.0,
                                "credit": amount > 0.0 and amount or 0.0,
                            }
                        )            
            if already_exists:
                amount = rec.ks_amount_discount
                if (
                    rec.ks_sales_discount_account_id
                    and (
                        rec.move_type == "out_invoice" or rec.move_type == "out_refund"
                    )
                    and amount > 0
                ):
                    if rec.move_type == "out_invoice":
                        already_exists.update(
                            {
                                "debit": amount > 0.0 and amount or 0.0,
                                "credit": amount < 0.0 and -amount or 0.0,
                            }
                        )
                    else:
                        already_exists.update(
                            {
                                "debit": amount < 0.0 and -amount or 0.0,
                                "credit": amount > 0.0 and amount or 0.0,
                            }
                        )
                if (
                    rec.ks_purchase_discount_account_id
                    and (rec.move_type == "in_invoice" or rec.move_type == "in_refund")
                    and amount > 0
                ):
                    if rec.move_type == "in_invoice":
                        already_exists.update(
                            {
                                "debit": amount < 0.0 and -amount or 0.0,
                                "credit": amount > 0.0 and amount or 0.0,
                            }
                        )
                    else:
                        already_exists.update(
                            {
                                "debit": amount > 0.0 and amount or 0.0,
                                "credit": amount < 0.0 and -amount or 0.0,
                            }
                        )
                total_balance = sum(other_lines.mapped("balance"))
                total_amount_currency = sum(other_lines.mapped("amount_currency"))
                if not sum(terms_lines.mapped("debit")) == rec.amount_total_signed:
                    discount_percent = 0.0
                    total_discount = 0.0
                    for record in range(0, len(terms_lines)):
                        if len(self.invoice_payment_term_id.line_ids) >= len(
                            terms_lines
                        ):
                            if self.invoice_payment_term_id.line_ids[
                                record
                            ].value_amount:
                                total_discount += self.invoice_payment_term_id.line_ids[
                                    record
                                ].value_amount
                            else:
                                discount_percent = 100 - total_discount
                            terms_lines[record].update(
                                {
                                    "amount_currency": -total_amount_currency,
                                    "debit": (
                                        self.amount_total
                                        * (
                                            self.invoice_payment_term_id.line_ids[
                                                record
                                            ].value_amount
                                            if not discount_percent
                                            else discount_percent
                                        )
                                        / 100
                                    )
                                    if total_balance < 0.0
                                    else 0.0,
                                    "credit": (
                                        (
                                            self.amount_total
                                            * self.invoice_payment_term_id.line_ids[
                                                record
                                            ].value_amount
                                        )
                                        / 100
                                    )
                                    if total_balance > 0.0
                                    else 0.0,
                                }
                            )
                        else:
                            terms_lines[record].update(
                                {
                                    "amount_currency": -total_amount_currency,
                                    "debit": self.amount_total
                                    if total_balance < 0.0
                                    else 0.0,
                                    "credit": self.amount_total
                                    if total_balance > 0.0
                                    else 0.0,
                                }
                            )
                else:
                    for record in terms_lines:
                        if rec.ks_global_discount_type == "percent":
                            record.update(
                                {
                                    "amount_currency": -total_amount_currency,
                                    "debit": (
                                        record.debit
                                        - (
                                            (
                                                record.debit
                                                * self.ks_global_discount_rate
                                            )
                                            / 100
                                        )
                                    )
                                    if total_balance < 0.0
                                    else 0.0,
                                    "credit": (
                                        record.credit
                                        - (
                                            (
                                                record.credit
                                                * self.ks_global_discount_rate
                                            )
                                            / 100
                                        )
                                    )
                                    if total_balance > 0.0
                                    else 0.0,
                                }
                            )
                        else:
                            discount = rec.ks_global_discount_rate / len(terms_lines)
                            record.update(
                                {
                                    "amount_currency": -total_amount_currency,
                                    "debit": (record.debit - discount)
                                    if total_balance < 0.0
                                    else 0.0,
                                    "credit": (record.credit - discount)
                                    if total_balance > 0.0
                                    else 0.0,
                                }
                            )
            if not already_exists and rec.ks_global_discount_rate > 0:
                in_draft_mode = self != self._origin
                if not in_draft_mode:
                    rec._recompute_universal_discount_lines()

    @api.onchange("ks_global_discount_rate", "ks_global_discount_type", "line_ids")
    def _recompute_universal_discount_lines(self):
        """This Function Create The General Entries for Universal Discount"""
        for rec in self:
            type_list = ["out_invoice", "out_refund", "in_invoice", "in_refund"]
            if rec.ks_global_discount_rate > 0 and rec.move_type in type_list:
                if rec.is_invoice(include_receipts=True):
                    in_draft_mode = self != self._origin
                    ks_name = "Universal Discount "
                    if rec.ks_global_discount_type == "amount":
                        ks_value = "of amount #" + str(self.ks_global_discount_rate)
                    elif rec.ks_global_discount_type == "percent":
                        ks_value = " @" + str(self.ks_global_discount_rate) + "%"
                    else:
                        ks_value = ""
                    ks_name = ks_name + ks_value

                    if not self.line_ids:
                        continue

                    terms_lines = self.line_ids.filtered(
                        lambda line: line.account_id.user_type_id.type
                        in ("receivable", "payable")
                    )
                    already_exists = self.line_ids.filtered(
                        lambda line: line.name
                        and line.name.find("Universal Discount") == 0
                    )
                    if already_exists:
                        amount = self.ks_amount_discount
                        if self.ks_sales_discount_account_id and (
                            self.move_type == "out_invoice"
                            or self.move_type == "out_refund"
                        ):
                            if self.move_type == "out_invoice":
                                already_exists.update(
                                    {
                                        "name": ks_name,
                                        "debit": amount > 0.0 and amount or 0.0,
                                        "credit": amount < 0.0 and -amount or 0.0,
                                    }
                                )
                            else:
                                already_exists.update(
                                    {
                                        "name": ks_name,
                                        "debit": amount < 0.0 and -amount or 0.0,
                                        "credit": amount > 0.0 and amount or 0.0,
                                    }
                                )
                        if self.ks_purchase_discount_account_id and (
                            self.move_type == "in_invoice"
                            or self.move_type == "in_refund"
                        ):
                            if self.move_type == "in_invoice":
                                already_exists.update(
                                    {
                                        "name": ks_name,
                                        "debit": amount < 0.0 and -amount or 0.0,
                                        "credit": amount > 0.0 and amount or 0.0,
                                    }
                                )
                            else:
                                already_exists.update(
                                    {
                                        "name": ks_name,
                                        "debit": amount > 0.0 and amount or 0.0,
                                        "credit": amount < 0.0 and -amount or 0.0,
                                    }
                                )
                    else:
                        create_method = (
                            in_draft_mode
                            and self.env["account.move.line"].new
                            or self.env["account.move.line"].create
                        )

                        if self.ks_sales_discount_account_id and (
                            self.move_type == "out_invoice"
                            or self.move_type == "out_refund"
                        ):
                            amount = self.ks_amount_discount
                            dictionary = {
                                "move_name": self.name,
                                "name": ks_name,
                                "price_unit": self.ks_amount_discount,
                                "quantity": 1,
                                "debit": amount < 0.0 and -amount or 0.0,
                                "credit": amount > 0.0 and amount or 0.0,
                                "account_id": self.ks_sales_discount_account_id,
                                "move_id": self._origin,
                                "date": self.date,
                                "exclude_from_invoice_tab": True,
                                "partner_id": terms_lines.partner_id.id,
                                "company_id": terms_lines.company_id.id,
                                "company_currency_id": terms_lines.company_currency_id.id,
                            }
                            if self.move_type == "out_invoice":
                                dictionary.update(
                                    {
                                        "debit": amount > 0.0 and amount or 0.0,
                                        "credit": amount < 0.0 and -amount or 0.0,
                                    }
                                )
                            else:
                                dictionary.update(
                                    {
                                        "debit": amount < 0.0 and -amount or 0.0,
                                        "credit": amount > 0.0 and amount or 0.0,
                                    }
                                )
                            if in_draft_mode:
                                self.line_ids += create_method(dictionary)
                                # Updation of Invoice Line Id
                                duplicate_id = self.invoice_line_ids.filtered(
                                    lambda line: line.name
                                    and line.name.find("Universal Discount") == 0
                                )
                                self.invoice_line_ids = (
                                    self.invoice_line_ids - duplicate_id
                                )
                            else:
                                dictionary.update(
                                    {
                                        "price_unit": 0.0,
                                        "debit": 0.0,
                                        "credit": 0.0,
                                    }
                                )
                                self.line_ids = [(0, 0, dictionary)]

                        if self.ks_purchase_discount_account_id and (
                            self.move_type == "in_invoice"
                            or self.move_type == "in_refund"
                        ):
                            amount = self.ks_amount_discount
                            dictionary = {
                                "move_name": self.name,
                                "name": ks_name,
                                "price_unit": self.ks_amount_discount,
                                "quantity": 1,
                                "debit": amount > 0.0 and amount or 0.0,
                                "credit": amount < 0.0 and -amount or 0.0,
                                "account_id": self.ks_purchase_discount_account_id,
                                "move_id": self.id,
                                "date": self.date,
                                "exclude_from_invoice_tab": True,
                                "partner_id": terms_lines.partner_id.id,
                                "company_id": terms_lines.company_id.id,
                                "company_currency_id": terms_lines.company_currency_id.id,
                            }

                            if self.move_type == "in_invoice":
                                dictionary.update(
                                    {
                                        "debit": amount < 0.0 and -amount or 0.0,
                                        "credit": amount > 0.0 and amount or 0.0,
                                    }
                                )
                            else:
                                dictionary.update(
                                    {
                                        "debit": amount > 0.0 and amount or 0.0,
                                        "credit": amount < 0.0 and -amount or 0.0,
                                    }
                                )
                            if in_draft_mode:
                                self.line_ids += create_method(dictionary)
                                # Updation of Invoice Line Id
                                duplicate_id = self.invoice_line_ids.filtered(
                                    lambda line: line.name
                                    and line.name.find("Universal Discount") == 0
                                )
                                self.invoice_line_ids = (
                                    self.invoice_line_ids - duplicate_id
                                )
                            else:
                                dictionary.update(
                                    {
                                        "price_unit": 0.0,
                                        "debit": 0.0,
                                        "credit": 0.0,
                                    }
                                )
                                self.line_ids = [(0, 0, dictionary)]

                    if in_draft_mode:
                        # Update the payement account amount
                        terms_lines = self.line_ids.filtered(
                            lambda line: line.account_id.user_type_id.type
                            in ("receivable", "payable")
                        )
                        other_lines = self.line_ids.filtered(
                            lambda line: line.account_id.user_type_id.type
                            not in ("receivable", "payable")
                        )

                        if (
                            self.move_type == "in_invoice"
                            or self.move_type == "in_refund"
                        ):
                            amount = -amount

                        total_balance = sum(other_lines.mapped("balance"))
                        total_amount_currency = sum(
                            other_lines.mapped("amount_currency")
                        )
                        for record in terms_lines:
                            if rec.ks_global_discount_type == "percent":
                                record.update(
                                    {
                                        "amount_currency": -total_amount_currency,
                                        "debit": abs(
                                            record.price_total
                                            - (
                                                (
                                                    record.price_total
                                                    * rec.ks_global_discount_rate
                                                )
                                                / 100
                                            )
                                        )
                                        if total_balance < 0.0
                                        else 0.0,
                                        "credit": abs(
                                            record.price_total
                                            - (
                                                (
                                                    record.price_total
                                                    * rec.ks_global_discount_rate
                                                )
                                                / 100
                                            )
                                        )
                                        if total_balance > 0.0
                                        else 0.0,
                                    }
                                )
                            elif rec.ks_global_discount_type == "amount":
                                discount = rec.ks_global_discount_rate / len(
                                    terms_lines
                                )
                                record.update(
                                    {
                                        "amount_currency": -total_amount_currency,
                                        "debit": abs(record.price_total + discount)
                                        if total_balance < 0.0
                                        else 0.0,
                                        "credit": abs(record.price_total + discount)
                                        if total_balance > 0.0
                                        else 0.0,
                                    }
                                )
                    else:
                        terms_lines = self.line_ids.filtered(
                            lambda line: line.account_id.user_type_id.type
                            in ("receivable", "payable")
                        )
                        other_lines = self.line_ids.filtered(
                            lambda line: line.account_id.user_type_id.type
                            not in ("receivable", "payable")
                        )
                        already_exists = self.line_ids.filtered(
                            lambda line: line.name
                            and line.name.find("Universal Discount") == 0
                        )

                        if (
                            self.move_type == "in_invoice"
                            or self.move_type == "in_refund"
                        ):
                            amount = -amount

                        total_balance = sum(other_lines.mapped("balance")) + amount
                        total_amount_currency = sum(
                            other_lines.mapped("amount_currency")
                        )
                        line_ids = []
                        dict1 = {
                            "debit": amount > 0.0 and amount or 0.0,
                            "credit": amount < 0.0 and -amount or 0.0,
                        }
                        line_ids.append((1, already_exists.id, dict1))
                        dict2 = {
                            "debit": total_balance < 0.0 and -total_balance or 0.0,
                            "credit": total_balance > 0.0 and total_balance or 0.0,
                        }
                        # for records in already_exists:
                        #     records.update(dict1)
                        for record in terms_lines:
                            if rec.ks_global_discount_type == "percent":
                                dict2 = {
                                    "amount_currency": -total_amount_currency,
                                    "debit": abs(
                                        record.price_total
                                        - (
                                            (
                                                record.price_total
                                                * rec.ks_global_discount_rate
                                            )
                                            / 100
                                        )
                                    )
                                    if total_balance < 0.0
                                    else 0.0,
                                    "credit": abs(
                                        record.price_total
                                        - (
                                            (
                                                record.price_total
                                                * rec.ks_global_discount_rate
                                            )
                                            / 100
                                        )
                                    )
                                    if total_balance > 0.0
                                    else 0.0,
                                }
                            elif rec.ks_global_discount_type == "amount":
                                discount = rec.ks_global_discount_rate / len(
                                    terms_lines
                                )
                                dict2 = {
                                    "amount_currency": -total_amount_currency,
                                    "debit": abs(record.price_total + discount)
                                    if total_balance < 0.0
                                    else 0.0,
                                    "credit": abs(record.price_total + discount)
                                    if total_balance > 0.0
                                    else 0.0,
                                }
                            line_ids.append((1, record.id, dict2))
                        # self.line_ids = [(1, already_exists.id, dict1), (1, terms_lines.id, dict2)]
                        self.line_ids = line_ids

            elif self.ks_global_discount_rate <= 0:
                already_exists = self.line_ids.filtered(
                    lambda line: line.name and line.name.find("Universal Discount") == 0
                )
                if already_exists:
                    self.line_ids -= already_exists
                    terms_lines = self.line_ids.filtered(
                        lambda line: line.account_id.user_type_id.type
                        in ("receivable", "payable")
                    )
                    other_lines = self.line_ids.filtered(
                        lambda line: line.account_id.user_type_id.type
                        not in ("receivable", "payable")
                    )
                    total_balance = sum(other_lines.mapped("balance"))
                    total_amount_currency = sum(other_lines.mapped("amount_currency"))
                    terms_lines.update(
                        {
                            "amount_currency": -total_amount_currency,
                            "debit": total_balance < 0.0 and -total_balance or 0.0,
                            "credit": total_balance > 0.0 and total_balance or 0.0,
                        }
                    )
