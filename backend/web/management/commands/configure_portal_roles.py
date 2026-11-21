from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the portal's operational roles and add their baseline permissions. Existing assignments are retained."

    def handle(self, *args, **kwargs):
        groups = {
            "Technician": {
                "view": "jobassignment jobequipment servicejob serviceappointment inspectionreport inspectionfinding jobcompletioncard completionworkitem serviceattachment serviceupdate generateddocument employeeprofile",
                "add": "inspectionreport inspectionfinding jobcompletioncard completionworkitem serviceattachment serviceupdate generateddocument",
                "change": "inspectionreport inspectionfinding jobcompletioncard completionworkitem",
                "special": "respond_to_assignment",
            },
            "Sales Executive": {
                "view": "customercompany customersite equipment servicejob jobequipment inspectionreport jobcompletioncard generateddocument documentapproval quotation quotationline quoteemailrequest emailapprovalsubmission customerapprover",
                "add": "emailapprovalsubmission generateddocument",
                "special": "verify_email_approval verify_quotation_reply",
            },
            "Manager": {
                "view": "region customercompany customersite equipment employeeprofile amccontract amcequipmentcoverage servicejob jobequipment serviceappointment reschedulerequest jobassignment inspectionreport jobcompletioncard pendingservicecharge quotation quotationline invoice invoiceline payment creditnote rentalitem rentalorder rentalorderline generateddocument reminderpolicy",
                "add": "customersite equipment amccontract amcequipmentcoverage servicejob jobequipment serviceappointment pendingservicecharge quotation quotationline invoice invoiceline payment creditnote rentalitem generateddocument serviceupdate reminderpolicy",
                "change": "customercompany customersite equipment amccontract amcequipmentcoverage servicejob jobequipment serviceappointment reschedulerequest pendingservicecharge quotation quotationline invoice invoiceline payment rentalitem rentalorder reminderpolicy",
                "special": "assign_technician",
            },
        }
        groups["Admin"] = groups["Manager"]
        for name, rules in groups.items():
            wanted = {
                f"{verb}_{model}"
                for verb, names in rules.items()
                if verb != "special"
                for model in names.split()
            }
            wanted.update(rules.get("special", "").split())
            permissions = list(
                Permission.objects.filter(content_type__app_label="web", codename__in=wanted)
            )
            missing = wanted - {p.codename for p in permissions}
            if missing:
                raise CommandError(
                    "Migrate first. Missing permissions: " + ", ".join(sorted(missing))
                )
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.add(*permissions)
            self.stdout.write(f"{name}: {len(permissions)} portal permissions configured.")
