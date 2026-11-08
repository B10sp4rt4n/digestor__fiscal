"""Cumulative allocation with an immediately liquidatable remainder.
New declarations use base6/tax2. Historical six-place tax is not rewritten.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from app.services.rep_validation import tax_limits

VERSION='rep20-cumulative-tax-cents/2'
CENT=Decimal('.01'); SIX=Decimal('.000001')


def allocate(snapshot, amount, balance, allocated_base, allocated_tax):
    original_base,original_tax,total=(Decimal(snapshot[k]) for k in ('base','tax','total'))
    if allocated_tax != allocated_tax.quantize(CENT):
        raise ValueError('El historial usa impuesto fraccionario previo; requiere conciliación sin reescribir evidencia')
    remaining=balance-amount
    if amount<=0 or remaining<0: raise ValueError('Pago fuera del saldo')
    with localcontext() as ctx:
        ctx.prec=50
        cumulative_paid=total-remaining
        target_base=(original_base*cumulative_paid/total).quantize(SIX,rounding=ROUND_HALF_UP)
        base=target_base-allocated_base if remaining else original_base-allocated_base
        future_base=original_base-allocated_base-base
        available_tax=original_tax-allocated_tax
        low,high=tax_limits(f'{base:.6f}')
        if remaining:
            future_low,future_high=tax_limits(f'{future_base:.6f}')
            low=max(low, available_tax-future_high,Decimal(0))
            high=min(high, available_tax-future_low,available_tax)
        else:
            low=max(low,available_tax); high=min(high,available_tax)
        target_tax=(original_tax*cumulative_paid/total).quantize(CENT,rounding=ROUND_HALF_UP)-allocated_tax
        if low>high or base<=0: raise ValueError('No existe reparto que respete límites fiscales y liquidación')
        tax=min(high,max(low,target_tax))
        if tax<0 or tax!=tax.quantize(CENT): raise ValueError('Impuesto restante no representable en centavos')
        return base,tax
