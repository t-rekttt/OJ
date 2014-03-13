from django.views.generic import TemplateView

from .register import RegistrationView, ActivationView
from .user import users


class TemplateView(TemplateView):
    title = None

    def get_context_data(self, **kwargs):
        if 'title' not in kwargs and self.title is not None:
            kwargs['title'] = self.title
        return super(TemplateView, self).get_context_data(**kwargs)
