import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import ManagerCalendar from './ManagerCalendar'
import EmployeeView from './EmployeeView'
import MonthlyReport from './MonthlyReport'

export default function ManagerView() {
  return (
    <Tabs defaultValue="calendar">
      <TabsList className="mb-4">
        <TabsTrigger value="calendar">Team Schedule</TabsTrigger>
        <TabsTrigger value="availability">My Availability</TabsTrigger>
        <TabsTrigger value="report">Monthly Report</TabsTrigger>
      </TabsList>
      <TabsContent value="calendar">
        <ManagerCalendar />
      </TabsContent>
      <TabsContent value="availability">
        <EmployeeView />
      </TabsContent>
      <TabsContent value="report">
        <MonthlyReport />
      </TabsContent>
    </Tabs>
  )
}
