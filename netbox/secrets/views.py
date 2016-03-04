from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import permission_required, login_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.urlresolvers import reverse
from django.db import transaction, IntegrityError
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator

from utilities.error_handlers import handle_protectederror
from utilities.forms import ConfirmationForm
from utilities.views import BulkEditView, BulkDeleteView, ObjectListView

from .decorators import userkey_required
from .filters import SecretFilter
from .forms import SecretForm, SecretImportForm, SecretBulkEditForm, SecretBulkDeleteForm, SecretFilterForm
from .models import Secret, UserKey
from .tables import SecretTable, SecretBulkEditTable


#
# Secrets
#

@method_decorator(login_required, name='dispatch')
class SecretListView(ObjectListView):
    queryset = Secret.objects.select_related('role').prefetch_related('parent')
    filter = SecretFilter
    filter_form = SecretFilterForm
    table = SecretTable
    edit_table = SecretBulkEditTable
    edit_table_permissions = ['secrets.change_secret', 'secrets.delete_secret']
    template_name = 'secrets/secret_list.html'


@login_required
def secret(request, pk):

    secret = get_object_or_404(Secret, pk=pk)

    return render(request, 'secrets/secret.html', {
        'secret': secret,
    })


@permission_required('secrets.add_secret')
@userkey_required()
def secret_add(request, parent_model, parent_pk):

    # Retrieve parent object
    parent_cls = apps.get_model(parent_model)
    parent = get_object_or_404(parent_cls, pk=parent_pk)

    secret = Secret(parent=parent)
    uk = UserKey.objects.get(user=request.user)

    if request.method == 'POST':
        form = SecretForm(request.POST, instance=secret)
        if form.is_valid():

            # Retrieve the master key from the current user's UserKey
            master_key = uk.get_master_key(form.cleaned_data['private_key'])
            if master_key is None:
                form.add_error(None, "Invalid private key! Unable to encrypt secret data.")

            # Create and encrypt the new Secret
            else:
                secret = form.save(commit=False)
                secret.plaintext = str(form.cleaned_data['plaintext'])
                secret.encrypt(master_key)
                secret.save()

                messages.success(request, "Added new secret: {0}".format(secret))
                if '_addanother' in request.POST:
                    return redirect('secrets:secret_add')
                else:
                    return redirect('secrets:secret', pk=secret.pk)

    else:
        form = SecretForm(instance=secret)

    return render(request, 'secrets/secret_edit.html', {
        'secret': secret,
        'form': form,
        'cancel_url': parent.get_absolute_url(),
    })


@permission_required('secrets.change_secret')
@userkey_required()
def secret_edit(request, pk):

    secret = get_object_or_404(Secret, pk=pk)
    uk = UserKey.objects.get(user=request.user)

    if request.method == 'POST':
        form = SecretForm(request.POST, instance=secret)
        if form.is_valid():

            # Re-encrypt the Secret if a plaintext has been specified.
            if form.cleaned_data['plaintext']:

                # Retrieve the master key from the current user's UserKey
                master_key = uk.get_master_key(form.cleaned_data['private_key'])
                if master_key is None:
                    form.add_error(None, "Invalid private key! Unable to encrypt secret data.")

                # Create and encrypt the new Secret
                else:
                    secret = form.save(commit=False)
                    secret.plaintext = str(form.cleaned_data['plaintext'])
                    secret.encrypt(master_key)
                    secret.save()

            else:
                secret = form.save()

            messages.success(request, "Modified secret {0}".format(secret))
            return redirect('secrets:secret', pk=secret.pk)

    else:
        form = SecretForm(instance=secret)

    return render(request, 'secrets/secret_edit.html', {
        'secret': secret,
        'form': form,
        'cancel_url': reverse('secrets:secret', kwargs={'pk': secret.pk}),
    })


@permission_required('secrets.delete_secret')
def secret_delete(request, pk):

    secret = get_object_or_404(Secret, pk=pk)

    if request.method == 'POST':
        form = ConfirmationForm(request.POST)
        if form.is_valid():
            try:
                secret.delete()
                messages.success(request, "Secret {0} has been deleted".format(secret))
                return redirect('secrets:secret_list')
            except ProtectedError, e:
                handle_protectederror(secret, request, e)
                return redirect('secrets:secret', pk=secret.pk)

    else:
        form = ConfirmationForm()

    return render(request, 'secrets/secret_delete.html', {
        'secret': secret,
        'form': form,
        'cancel_url': reverse('secrets:secret', kwargs={'pk': secret.pk})
    })


@permission_required('secrets.add_secret')
@userkey_required()
def secret_import(request):

    uk = UserKey.objects.get(user=request.user)

    if request.method == 'POST':
        form = SecretImportForm(request.POST)
        if form.is_valid():

            new_secrets = []

            # Retrieve the master key from the current user's UserKey
            master_key = uk.get_master_key(form.cleaned_data['private_key'])
            if master_key is None:
                form.add_error(None, "Invalid private key! Unable to encrypt secret data.")

            else:
                try:
                    with transaction.atomic():
                        for secret in form.cleaned_data['csv']:
                            secret.encrypt(master_key)
                            secret.save()
                            new_secrets.append(secret)

                    secret_table = SecretTable(new_secrets)
                    messages.success(request, "Imported {} new secrets".format(len(new_secrets)))

                    return render(request, 'import_success.html', {
                        'secret_table': secret_table,
                    })

                except IntegrityError as e:
                    form.add_error('csv', "Record {}: {}".format(len(new_secrets) + 1, e.__cause__))

    else:
        form = SecretImportForm()

    return render(request, 'secrets/secret_import.html', {
        'form': form,
        'cancel_url': reverse('secrets:secret_list'),
    })


class SecretBulkEditView(PermissionRequiredMixin, BulkEditView):
    permission_required = 'secrets.change_secret'
    cls = Secret
    form = SecretBulkEditForm
    template_name = 'secrets/secret_bulk_edit.html'
    redirect_url = 'secrets:secret_list'

    def update_objects(self, pk_list, form):

        fields_to_update = {}
        for field in ['role', 'name']:
            if form.cleaned_data[field]:
                fields_to_update[field] = form.cleaned_data[field]

        updated_count = self.cls.objects.filter(pk__in=pk_list).update(**fields_to_update)
        messages.success(self.request, "Updated {} secrets".format(updated_count))


class SecretBulkDeleteView(PermissionRequiredMixin, BulkDeleteView):
    permission_required = 'secrets.delete_secret'
    cls = Secret
    form = SecretBulkDeleteForm
    template_name = 'secrets/secret_bulk_delete.html'
    redirect_url = 'secrets:secret_list'